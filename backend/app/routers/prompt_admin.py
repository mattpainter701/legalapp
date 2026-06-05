"""
Admin-only prompt management routes.

Provides CRUD for per-tenant prompt overrides and a test endpoint for
previewing prompt changes before saving.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin as _require_admin
from app.models.plugin import PromptOverride
from app.routers.plugins import VALID_PLUGINS
from app.services.plugins.prompts import PLUGIN_DISPLAY_NAMES, PLUGIN_SKILLS
from app.schemas.plugin import (
    PromptDetail,
    PromptInfo,
    PromptListResponse,
    PromptPluginTree,
    PromptResetResponse,
    PromptTestRequest,
    PromptTestResponse,
    PromptUpdate,
)
from app.services.plugins.prompts import ALL_DEFAULT_PROMPTS

router = APIRouter(prefix="/admin/prompts", tags=["admin-prompts"])


@router.get("", response_model=PromptListResponse)
async def list_prompts(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all prompts in a plugin -> skill tree, showing override status."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    # Fetch all overrides for this tenant in one query
    result = await db.execute(
        select(PromptOverride).where(
            PromptOverride.tenant_id == admin.tenant_id,
        )
    )
    overrides = result.scalars().all()
    override_map: dict[tuple[str, str], PromptOverride] = {}
    for o in overrides:
        override_map[(o.plugin_name, o.skill_name)] = o

    plugins = []
    for plugin_name, display_name in PLUGIN_DISPLAY_NAMES.items():
        skills = PLUGIN_SKILLS.get(plugin_name, [])
        skill_infos = []
        for skill_name in skills:
            override = override_map.get((plugin_name, skill_name))
            skill_infos.append(
                PromptInfo(
                    plugin_name=plugin_name,
                    skill_name=skill_name,
                    has_override=override is not None,
                    is_active=override.is_active if override else True,
                    updated_at=override.updated_at if override else None,
                )
            )
        plugins.append(
            PromptPluginTree(
                plugin_name=plugin_name,
                display_name=display_name,
                skills=skill_infos,
            )
        )

    return PromptListResponse(plugins=plugins)


@router.get("/{plugin}/{skill}", response_model=PromptDetail)
async def get_prompt_detail(
    plugin: str,
    skill: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get full prompt detail: default content + optional tenant override."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    _validate_prompt_path(plugin, skill)

    default_content = ALL_DEFAULT_PROMPTS.get((plugin, skill), "")

    result = await db.execute(
        select(PromptOverride).where(
            PromptOverride.tenant_id == admin.tenant_id,
            PromptOverride.plugin_name == plugin,
            PromptOverride.skill_name == skill,
        )
    )
    override = result.scalar_one_or_none()

    return PromptDetail(
        plugin_name=plugin,
        skill_name=skill,
        default_content=default_content,
        override_content=override.prompt_content if override else None,
        is_active=override.is_active if override else True,
        updated_at=override.updated_at if override else None,
    )


@router.put("/{plugin}/{skill}", response_model=PromptDetail)
async def upsert_prompt_override(
    plugin: str,
    skill: str,
    body: PromptUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create or update a tenant prompt override. Invalidates cache on write."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    _validate_prompt_path(plugin, skill)

    result = await db.execute(
        select(PromptOverride).where(
            PromptOverride.tenant_id == admin.tenant_id,
            PromptOverride.plugin_name == plugin,
            PromptOverride.skill_name == skill,
        )
    )
    override = result.scalar_one_or_none()

    if override is None:
        override = PromptOverride(
            id=uuid.uuid4(),
            tenant_id=admin.tenant_id,
            plugin_name=plugin,
            skill_name=skill,
            created_by=admin.id,
        )
        db.add(override)

    override.prompt_content = body.prompt_content
    override.is_active = body.is_active
    override.updated_by = admin.id
    override.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(override)

    # Invalidate cache
    from app.routers.plugins import prompt_resolver

    await prompt_resolver.invalidate(str(admin.tenant_id), plugin, skill)

    # Reload with current state
    return PromptDetail(
        plugin_name=plugin,
        skill_name=skill,
        default_content=ALL_DEFAULT_PROMPTS.get((plugin, skill), ""),
        override_content=override.prompt_content,
        is_active=override.is_active,
        updated_at=override.updated_at,
    )


@router.delete("/{plugin}/{skill}", response_model=PromptResetResponse)
async def reset_prompt_override(
    plugin: str,
    skill: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a tenant prompt override, restoring the code default."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    _validate_prompt_path(plugin, skill)

    result = await db.execute(
        select(PromptOverride).where(
            PromptOverride.tenant_id == admin.tenant_id,
            PromptOverride.plugin_name == plugin,
            PromptOverride.skill_name == skill,
        )
    )
    override = result.scalar_one_or_none()
    if override is None:
        raise HTTPException(
            status_code=404,
            detail="No override found — prompt is already using the code default",
        )

    await db.delete(override)
    await db.commit()

    # Invalidate cache
    from app.routers.plugins import prompt_resolver

    await prompt_resolver.invalidate(str(admin.tenant_id), plugin, skill)

    return PromptResetResponse(
        plugin_name=plugin,
        skill_name=skill,
        restored=True,
    )


@router.post("/{plugin}/{skill}/test", response_model=PromptTestResponse)
async def test_prompt(
    plugin: str,
    skill: str,
    body: PromptTestRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Test a prompt by running it through the LLM with sample input."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    _validate_prompt_path(plugin, skill)

    # Build system prompt like executor does, using provided content
    from app.services.plugins.prompts import WORK_PRODUCT_HEADER, UNIVERSAL_GUARDRAILS

    format_kwargs = {
        "work_product_header": WORK_PRODUCT_HEADER,
        "universal_guardrails": UNIVERSAL_GUARDRAILS,
        "practice_profile": "Test context — no practice profile loaded",
        "matter_context": "",
        "dsar_context": "",
        "jurisdiction": "Jurisdiction not specified",
        "chart_mode": "infringement",
    }

    try:
        system_prompt = body.prompt_content.format(**format_kwargs)
    except KeyError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Prompt contains unrecognized or missing template variable: {e}",
        )

    # Short-circuit: LLM might not be available offline, return the rendered prompt
    # as the "response" so the admin can verify formatting
    from app.services.llm import LLMService
    from app.services.llm_routing import resolve_llm_route

    llm = LLMService()
    route = await resolve_llm_route(db, admin.tenant_id, use_premium=False)
    try:
        response_text, tokens_in, tokens_out = await llm.complete(
            messages=[{"role": "user", "content": body.sample_input}],
            tenant_name="Admin Console",
            context=system_prompt,
            provider=route.provider,
            model=route.model,
        )
        model_used = route.model
    except Exception as e:
        # If LLM call fails, return the rendered prompt as a preview
        return PromptTestResponse(
            response_text=(
                f"[LLM call failed: {e}]\n\n--- Rendered prompt preview ---\n\n"
                f"{system_prompt[:5000]}"
            ),
            tokens_used=0,
            model_used="preview-only",
            gates_triggered=[],
        )

    return PromptTestResponse(
        response_text=response_text,
        tokens_used=tokens_in + tokens_out,
        model_used=model_used,
        gates_triggered=[],
    )


def _validate_prompt_path(plugin: str, skill: str) -> None:
    """Validate that the plugin and skill exist."""
    if plugin not in VALID_PLUGINS:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin}' not found. Valid: {sorted(VALID_PLUGINS)}",
        )
    valid_skills = PLUGIN_SKILLS.get(plugin, [])
    if skill not in valid_skills:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Skill '{skill}' not found for plugin '{plugin}'. "
                f"Valid skills: {valid_skills}"
            ),
        )
