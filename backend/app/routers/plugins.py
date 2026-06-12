"""
Legal practice plugin router.

Endpoints:
  GET  /plugins                             — list all plugins
  GET  /plugins/{plugin}/profile            — get practice profile
  PUT  /plugins/{plugin}/profile            — upsert practice profile
  POST /plugins/{plugin}/cold-start         — cold-start interview

  GET  /plugins/litigation/matters          — list matters portfolio
  POST /plugins/litigation/matters          — create matter
  GET  /plugins/litigation/matters/{id}     — get matter detail
  PATCH /plugins/litigation/matters/{id}    — update matter
  POST /plugins/litigation/matters/{id}/events — append event

  GET  /plugins/commercial/renewals         — list renewals
  POST /plugins/commercial/renewals         — create renewal
  PATCH /plugins/commercial/renewals/{id}   — update renewal status
  DELETE /plugins/commercial/renewals/{id}  — delete renewal

  POST /plugins/{plugin}/{skill}            — execute a skill (catch-all, registered last)

NOTE: Static resource routes are registered before the /{plugin}/{skill} catch-all so that
POST /litigation/matters and POST /commercial/renewals are not swallowed by the dynamic route.
"""

import uuid
import re
import json
import logging
from datetime import date, datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conversation import UsageRecord
from app.models.plugin import (
    Matter,
    MatterEvent,
    PracticeProfile,
    Renewal,
    TenantPluginEntitlement,
    TenantPluginSetup,
)
from app.models.tenant_credential import TenantCredential
from app.schemas.plugin import (
    MatterCreate,
    MatterEventCreate,
    MatterEventResponse,
    MatterResponse,
    MatterUpdate,
    PluginInfo,
    PluginEntitlementResponse,
    PluginEntitlementUpdate,
    PluginListResponse,
    PluginSetupHealth,
    PluginSetupResponse,
    PluginSetupUpsert,
    PracticeProfileResponse,
    PracticeProfileUpsert,
    RenewalCreate,
    RenewalResponse,
    RenewalUpdate,
    SkillRequest,
    SkillResponse,
)
from app.services.billing import calculate_cost
from app.services.cache import ExpertiseCacheManager
from app.services.conflict_check import run_conflict_check
from app.services.cloud_search import CloudSearchService
from app.services.llm import LLMService
from app.services.matter_context import MatterContextService
from app.services.plugins.executor import PluginExecutor
from app.services.plugins.manifest import (
    get_plugin_manifest,
    list_plugin_manifests,
    valid_plugin_names,
)
from app.services.plugins.prompts import PLUGIN_SKILLS
from app.services.plugins.prompt_resolver import PromptResolver
from app.services.rag import build_cloud_context
from app.services.retrieval_planner import RetrievalPlanner

settings = get_settings()
router = APIRouter(prefix="/plugins", tags=["plugins"])
logger = logging.getLogger(__name__)

# Module-level singletons (same pattern as chat router)
llm_service = LLMService()
plugin_cache_manager = ExpertiseCacheManager()
prompt_resolver = PromptResolver(plugin_cache_manager)
plugin_executor = PluginExecutor(llm_service, prompt_resolver)
matter_context_service = MatterContextService()
cloud_search_service = CloudSearchService()
retrieval_planner = RetrievalPlanner(llm_service)

# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_PLUGINS = valid_plugin_names()


def _slugify(text: str) -> str:
    """Turn a matter name into a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text[:180]  # keep well under the 200-char limit


def _matter_type(value: str | None) -> str:
    matter_type = (value or "").strip()
    return matter_type or "general"


def _renewal_urgency(days: int) -> str:
    if days <= 13:
        return "critical"
    if days <= 30:
        return "high"
    if days <= 60:
        return "medium"
    if days <= 90:
        return "low"
    return "monitor"


def _matter_to_response(matter: Matter) -> MatterResponse:
    return MatterResponse(
        id=str(matter.id),
        slug=matter.slug,
        matter_name=matter.matter_name,
        matter_type=matter.matter_type,
        role=matter.role,
        counterparty=matter.counterparty,
        jurisdiction=matter.jurisdiction,
        status=matter.status,
        risk_level=matter.risk_level,
        materiality=matter.materiality,
        conflicts_status=matter.conflicts_status,
        legal_hold_issued=matter.legal_hold_issued,
        is_closed=matter.is_closed,
        created_at=matter.created_at,
        updated_at=matter.updated_at,
    )


def _event_to_response(event: MatterEvent) -> MatterEventResponse:
    return MatterEventResponse(
        id=str(event.id),
        event_type=event.event_type,
        title=event.title,
        content=event.content,
        created_at=event.created_at,
    )


def _renewal_to_response(renewal: Renewal) -> RenewalResponse:
    today = date.today()
    renewal_date = renewal.renewal_date
    # renewal_date may be stored as date or datetime
    if isinstance(renewal_date, datetime):
        renewal_date = renewal_date.date()
    days = (renewal_date - today).days

    notice_deadline = renewal.notice_deadline
    if isinstance(notice_deadline, datetime):
        notice_deadline = notice_deadline.date()

    return RenewalResponse(
        id=str(renewal.id),
        contract_name=renewal.contract_name,
        vendor=renewal.vendor,
        renewal_date=renewal_date,
        notice_deadline=notice_deadline,
        contract_value_annual=(
            float(renewal.contract_value_annual)
            if renewal.contract_value_annual is not None
            else None
        ),
        auto_renewal=renewal.auto_renewal,
        status=renewal.status,
        days_until_renewal=days,
        urgency=_renewal_urgency(days),
        created_at=renewal.created_at,
    )


def _validate_plugin(plugin: str) -> None:
    if plugin not in valid_plugin_names():
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin}' not found. Valid plugins: {sorted(valid_plugin_names())}",
        )


def _entitlement_status(entitlement: TenantPluginEntitlement | None) -> str:
    if entitlement is None:
        return "available"
    return entitlement.status or "available"


def _is_purchased_status(status: str) -> bool:
    return status in {"purchased", "included", "trial"}


def _validate_entitlement_status(status: str) -> str:
    value = (status or "").strip().lower()
    allowed = {"available", "trial", "purchased", "included", "disabled", "locked"}
    if value not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid entitlement status. Valid statuses: {sorted(allowed)}",
        )
    return value


def _scope_set(row: TenantCredential | None) -> set[str]:
    if row is None or not row.scopes or not row.is_active:
        return set()
    return {s.strip() for s in row.scopes.split(" ") if s.strip()}


def _capabilities_from_credentials(credentials: list[TenantCredential]) -> set[str]:
    by_provider = {c.provider: c for c in credentials if c.is_active}
    microsoft = _scope_set(by_provider.get("microsoft"))
    google = _scope_set(by_provider.get("google"))

    capabilities: set[str] = set()
    if "Files.Read.All" in microsoft:
        capabilities.add("onedrive")
    if "Sites.Read.All" in microsoft:
        capabilities.add("sharepoint")
    if "Mail.Read" in microsoft:
        capabilities.add("outlook")
    if "Calendars.ReadWrite" in microsoft or "Calendars.Read" in microsoft:
        capabilities.add("calendar")
    if "https://www.googleapis.com/auth/drive.readonly" in google:
        capabilities.add("google_drive")
    if "https://www.googleapis.com/auth/gmail.readonly" in google:
        capabilities.add("gmail")
    if "https://www.googleapis.com/auth/calendar" in google:
        capabilities.add("calendar")
    return capabilities


def _providers_from_credentials(credentials: list[TenantCredential]) -> list[str]:
    providers = sorted({c.provider for c in credentials if c.is_active and c.scopes})
    return [p for p in providers if p in {"google", "microsoft"}]


def _setup_to_payload(setup: TenantPluginSetup | None) -> PluginSetupUpsert | None:
    if setup is None:
        return None
    return PluginSetupUpsert(
        jurisdictions=setup.jurisdictions or [],
        escalation_rules=setup.escalation_rules or {},
        approval_thresholds=setup.approval_thresholds or {},
        template_preferences=setup.template_preferences or {},
        cloud_bindings=setup.cloud_bindings or {},
        calendar_bindings=setup.calendar_bindings or {},
        house_style=setup.house_style or {},
        custom_config=setup.custom_config or {},
        generated_profile=setup.generated_profile,
        is_complete=setup.is_complete,
    )


def _build_setup_health(
    *,
    manifest,
    setup: TenantPluginSetup | None,
    profile: PracticeProfile | None,
    available_integrations: set[str],
) -> PluginSetupHealth:
    missing_required_fields = []
    if setup is None:
        missing_required_fields = [
            "jurisdictions",
            "escalation_rules",
            "approval_thresholds",
            "house_style",
        ]
    else:
        if not setup.jurisdictions:
            missing_required_fields.append("jurisdictions")
        if not setup.escalation_rules:
            missing_required_fields.append("escalation_rules")
        if not setup.approval_thresholds:
            missing_required_fields.append("approval_thresholds")
        if not setup.house_style:
            missing_required_fields.append("house_style")

    missing_required_integrations = [
        item
        for item in manifest.required_integrations
        if item not in available_integrations
    ]

    warnings = []
    if setup and setup.is_complete and missing_required_fields:
        warnings.append("Setup is marked complete but required fields are missing.")
    if setup and setup.generated_profile and not (profile and profile.profile_content):
        warnings.append("Generated profile has not been synced to the legacy profile.")

    if not manifest.setup_required:
        setup_status = "not-required"
    elif setup and setup.is_complete and not missing_required_fields:
        setup_status = "complete"
    elif setup:
        setup_status = "incomplete"
    elif profile and profile.is_complete:
        setup_status = "legacy-profile"
    else:
        setup_status = "not-started"

    return PluginSetupHealth(
        setup_status=setup_status,
        missing_required_fields=missing_required_fields,
        missing_required_integrations=missing_required_integrations,
        available_integrations=sorted(available_integrations),
        optional_integrations=manifest.optional_integrations,
        warnings=warnings,
    )


def _generate_structured_profile(
    plugin: str,
    display_name: str,
    setup: PluginSetupUpsert,
) -> str:
    payload = {
        "jurisdictions": setup.jurisdictions,
        "escalation_rules": setup.escalation_rules,
        "approval_thresholds": setup.approval_thresholds,
        "template_preferences": setup.template_preferences,
        "cloud_bindings": setup.cloud_bindings,
        "calendar_bindings": setup.calendar_bindings,
        "house_style": setup.house_style,
        "custom_config": setup.custom_config,
    }
    return (
        f"# {display_name} Structured Practice Profile\n\n"
        f"Plugin: {plugin}\n\n"
        "This profile was generated from structured tenant setup and should be "
        "treated as the controlling plugin playbook unless a more specific matter "
        "memory overrides it.\n\n"
        "```json\n"
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n"
        "```"
    )


async def _build_plugin_cloud_context(
    *,
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    tenant_name: str,
    question: str,
    matter_context: str | None,
) -> str:
    """Build additive cloud context for plugin execution.

    Cloud lookup is best-effort and must never block the plugin skill.
    """
    if not settings.CLOUD_SEARCH_ENABLED:
        return ""
    try:
        credential_result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.is_active.is_(True),
            )
        )
        credentials = list(credential_result.scalars().all())
        active_providers = _providers_from_credentials(credentials)
        if not active_providers:
            return ""

        plan = await retrieval_planner.plan(
            user_question=question,
            db=db,
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            matter_context=matter_context,
            active_providers=active_providers,
        )
        if not plan or not plan.get("should_search"):
            return ""

        hits = await cloud_search_service.search(
            db=db,
            plan=plan,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if not hits:
            return ""

        hits_with_content = await cloud_search_service.fetch_contents(
            db=db,
            hits=hits,
            tenant_id=tenant_id,
            max_chars=settings.CLOUD_SEARCH_HIT_CONTENT_CHARS,
        )
        serializable_hits = [
            {
                "hit": item["hit"].to_dict()
                if hasattr(item.get("hit"), "to_dict")
                else item.get("hit", {}),
                "content": item.get("content"),
            }
            for item in hits_with_content
        ]
        return await build_cloud_context(serializable_hits)
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Plugin listing (no path params)
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all plugins with their skills and profile status for this tenant."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # Fetch profiles, structured setup, entitlements, and integration credentials
    # for this tenant in compact queries.
    profile_result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
        )
    )
    profiles = profile_result.scalars().all()
    profile_map = {p.plugin_name: p for p in profiles}

    setup_result = await db.execute(
        select(TenantPluginSetup).where(
            TenantPluginSetup.tenant_id == user.tenant_id,
        )
    )
    setups = setup_result.scalars().all()
    setup_map = {s.plugin_name: s for s in setups}

    entitlement_result = await db.execute(
        select(TenantPluginEntitlement).where(
            TenantPluginEntitlement.tenant_id == user.tenant_id,
        )
    )
    entitlements = entitlement_result.scalars().all()
    entitlement_map = {e.plugin_name: e for e in entitlements}

    credential_result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == user.tenant_id,
            TenantCredential.is_active.is_(True),
        )
    )
    available_integrations = _capabilities_from_credentials(
        list(credential_result.scalars().all())
    )

    plugins = []
    for manifest in list_plugin_manifests():
        profile = profile_map.get(manifest.plugin_name)
        setup = setup_map.get(manifest.plugin_name)
        entitlement = entitlement_map.get(manifest.plugin_name)
        entitlement_status = _entitlement_status(entitlement)
        has_profile = profile is not None and bool(profile.profile_content)
        profile_is_complete = profile.is_complete if profile else False
        health = _build_setup_health(
            manifest=manifest,
            setup=setup,
            profile=profile,
            available_integrations=available_integrations,
        )

        plugins.append(
            PluginInfo(
                id=manifest.plugin_name,
                plugin_id=manifest.plugin_name,
                name=manifest.plugin_name,
                plugin_name=manifest.plugin_name,
                display_name=manifest.display_name,
                category=manifest.category,
                description=manifest.description,
                skills=manifest.skills,
                matter_types=manifest.matter_types,
                primary_route=manifest.primary_route,
                required_integrations=manifest.required_integrations,
                optional_integrations=manifest.optional_integrations,
                available_integrations=health.available_integrations,
                missing_required_integrations=health.missing_required_integrations,
                supports_matter_assignment=manifest.supports_matter_assignment,
                setup_required=manifest.setup_required,
                entitlement_status=entitlement_status,
                is_purchased=_is_purchased_status(entitlement_status),
                is_trial=entitlement_status == "trial",
                is_locked=entitlement_status in {"disabled", "locked"},
                setup_status=health.setup_status,
                has_profile=has_profile,
                profile_is_complete=profile_is_complete,
            )
        )

    return PluginListResponse(plugins=plugins)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Static resource routes (registered BEFORE /{plugin}/{skill})
# These must come before the catch-all dynamic route to avoid being swallowed.
# ═══════════════════════════════════════════════════════════════════════════════

# ── Litigation Matters ────────────────────────────────────────────────────────


@router.get("/litigation/matters", response_model=List[MatterResponse])
async def list_matters(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all matters for the tenant, sorted by updated_at descending."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Matter)
        .where(Matter.tenant_id == user.tenant_id)
        .order_by(Matter.updated_at.desc())
    )
    matters = result.scalars().all()
    return [_matter_to_response(m) for m in matters]


@router.post("/litigation/matters", response_model=MatterResponse, status_code=201)
async def create_matter(
    body: MatterCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new matter and trigger the intake workflow."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # Generate a unique slug within this tenant
    base_slug = _slugify(body.matter_name)
    slug = base_slug
    counter = 1
    while True:
        existing = await db.execute(
            select(Matter).where(
                Matter.tenant_id == user.tenant_id,
                Matter.slug == slug,
            )
        )
        if existing.scalar_one_or_none() is None:
            break
        slug = f"{base_slug}-{counter}"
        counter += 1

    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        slug=slug,
        matter_name=body.matter_name,
        matter_type=_matter_type(body.matter_type),
        role=body.role,
        counterparty=body.counterparty,
        jurisdiction=body.jurisdiction,
        source=body.source,
        status="threatened",
        conflicts_status="not-run",
        legal_hold_issued=False,
        is_closed=False,
    )
    db.add(matter)
    await db.flush()

    # Append an initial event
    event = MatterEvent(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        event_type="intake",
        title="Matter created",
        content=(
            f"Matter '{body.matter_name}' created. "
            f"Counterparty: {body.counterparty}. Source: {body.source}."
        ),
        created_by=user.id,
    )
    db.add(event)

    await db.commit()
    await db.refresh(matter)

    # Auto-run conflict check against counterparty name
    try:
        names = [body.counterparty] if body.counterparty else []
        check = await run_conflict_check(
            db=db,
            tenant_id=user.tenant_id,
            names=names,
            emails=[],
            exclude_matter_ids=[matter.id],
        )
        matter.conflicts_status = "clear" if check["clear"] else "conflict-found"
        await db.commit()
        await db.refresh(matter)
    except Exception:
        # Non-fatal: conflict check failure must not block matter creation
        pass

    # Auto-create cloud folders for this matter (non-fatal)
    try:
        from app.models.tenant import Tenant

        tenant_result = await db.execute(
            select(Tenant).where(Tenant.id == user.tenant_id)
        )
        tenant = tenant_result.scalar_one_or_none()
        if tenant and tenant.cloud_root_folder:
            from app.services.cloud_init import initialize_matter_folders

            cloud_folder = await initialize_matter_folders(
                db, str(user.tenant_id), matter.slug, tenant.cloud_root_folder
            )
            if cloud_folder:
                matter.cloud_folder = {**(matter.cloud_folder or {}), **cloud_folder}
                await db.commit()
                await db.refresh(matter)
    except Exception:
        logger.warning(
            "Failed to initialize cloud folders for plugin matter %s",
            matter.id,
            exc_info=True,
        )

    return _matter_to_response(matter)


@router.post(
    "/litigation/matters/{matter_id}/conflict-check",
    response_model=MatterResponse,
)
async def run_matter_conflict_check(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Manually re-run the conflict check for a matter and update conflicts_status."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    names = [matter.counterparty] if matter.counterparty else []
    check = await run_conflict_check(
        db=db,
        tenant_id=user.tenant_id,
        names=names,
        emails=[],
    )
    matter.conflicts_status = "clear" if check["clear"] else "conflict-found"
    matter.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(matter)
    return _matter_to_response(matter)


@router.get("/litigation/matters/{matter_id}", response_model=MatterResponse)
async def get_matter(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get a single matter by ID."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    return _matter_to_response(matter)


@router.patch("/litigation/matters/{matter_id}", response_model=MatterResponse)
async def update_matter(
    matter_id: str,
    body: MatterUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Partially update a matter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(matter, field, value)
    matter.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(matter)
    return _matter_to_response(matter)


@router.post(
    "/litigation/matters/{matter_id}/events",
    response_model=MatterEventResponse,
    status_code=201,
)
async def append_matter_event(
    matter_id: str,
    body: MatterEventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Append an event to the matter's event log."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # Verify matter belongs to this tenant
    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    event = MatterEvent(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        event_type=body.event_type,
        title=body.title,
        content=body.content,
        created_by=user.id,
    )
    db.add(event)

    # Bump matter updated_at
    matter.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(event)
    return _event_to_response(event)


# ── Commercial Renewals ───────────────────────────────────────────────────────


@router.get("/commercial/renewals", response_model=List[RenewalResponse])
async def list_renewals(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all renewals sorted by urgency (soonest first)."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Renewal)
        .where(Renewal.tenant_id == user.tenant_id)
        .order_by(Renewal.renewal_date.asc())
    )
    renewals = result.scalars().all()
    return [_renewal_to_response(r) for r in renewals]


@router.post("/commercial/renewals", response_model=RenewalResponse, status_code=201)
async def create_renewal(
    body: RenewalCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a contract renewal to the register."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    renewal = Renewal(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        contract_name=body.contract_name,
        vendor=body.vendor,
        renewal_date=body.renewal_date,
        notice_deadline=body.notice_deadline,
        contract_value_annual=body.contract_value_annual,
        auto_renewal=body.auto_renewal,
        business_owner=body.business_owner,
        business_owner_email=body.business_owner_email,
        notes=body.notes,
        status="pending",
    )
    db.add(renewal)
    await db.commit()
    await db.refresh(renewal)
    return _renewal_to_response(renewal)


@router.patch("/commercial/renewals/{renewal_id}", response_model=RenewalResponse)
async def update_renewal(
    renewal_id: str,
    body: RenewalUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update renewal status or notes."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Renewal).where(
            Renewal.id == renewal_id,
            Renewal.tenant_id == user.tenant_id,
        )
    )
    renewal = result.scalar_one_or_none()
    if renewal is None:
        raise HTTPException(status_code=404, detail="Renewal not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(renewal, field, value)
    renewal.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(renewal)
    return _renewal_to_response(renewal)


@router.delete("/commercial/renewals/{renewal_id}", status_code=204)
async def delete_renewal(
    renewal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a renewal entry."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Renewal).where(
            Renewal.id == renewal_id,
            Renewal.tenant_id == user.tenant_id,
        )
    )
    renewal = result.scalar_one_or_none()
    if renewal is None:
        raise HTTPException(status_code=404, detail="Renewal not found")

    await db.delete(renewal)
    await db.commit()


# NOTE: Mediation case endpoints used to live here as a minimal skeleton. They
# have been superseded by the dedicated Mediation Platform module
# (``app/routers/mediation.py`` + ``app/routers/mediation_portal.py``), which
# owns the ``/api/plugins/mediation`` paths.


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Dynamic plugin routes (/{plugin}/...) — registered AFTER statics
# ═══════════════════════════════════════════════════════════════════════════════

# ── Practice Profile CRUD ─────────────────────────────────────────────────────


@router.put("/{plugin}/entitlement", response_model=PluginEntitlementResponse)
async def upsert_plugin_entitlement(
    plugin: str,
    body: PluginEntitlementUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Admin-only local entitlement control for plugin add-ons.

    This records operational purchase/trial/lock state. Stripe automation can
    later write the same table without changing the catalog contract.
    """
    _validate_plugin(plugin)
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    await set_tenant_context(db, str(user.tenant_id))

    status = _validate_entitlement_status(body.status)

    result = await db.execute(
        select(TenantPluginEntitlement).where(
            TenantPluginEntitlement.tenant_id == user.tenant_id,
            TenantPluginEntitlement.plugin_name == plugin,
        )
    )
    entitlement = result.scalar_one_or_none()

    if entitlement is None:
        entitlement = TenantPluginEntitlement(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            plugin_name=plugin,
        )
        db.add(entitlement)

    entitlement.status = status
    entitlement.source = body.source or "admin"
    entitlement.seat_limit = body.seat_limit
    entitlement.config = body.config or {}
    entitlement.expires_at = body.expires_at
    entitlement.starts_at = entitlement.starts_at or datetime.now(timezone.utc)
    entitlement.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(entitlement)

    return PluginEntitlementResponse(
        plugin_name=plugin,
        status=entitlement.status,
        source=entitlement.source,
        seat_limit=entitlement.seat_limit,
        config=entitlement.config or {},
        expires_at=entitlement.expires_at,
        updated_at=entitlement.updated_at,
    )


@router.get("/{plugin}/setup", response_model=PluginSetupResponse)
async def get_plugin_setup(
    plugin: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get structured tenant setup and health for a plugin."""
    _validate_plugin(plugin)
    manifest = get_plugin_manifest(plugin)
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    setup_result = await db.execute(
        select(TenantPluginSetup).where(
            TenantPluginSetup.tenant_id == user.tenant_id,
            TenantPluginSetup.plugin_name == plugin,
        )
    )
    setup = setup_result.scalar_one_or_none()

    profile_result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
            PracticeProfile.plugin_name == plugin,
        )
    )
    profile = profile_result.scalar_one_or_none()

    credential_result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == user.tenant_id,
            TenantCredential.is_active.is_(True),
        )
    )
    available_integrations = _capabilities_from_credentials(
        list(credential_result.scalars().all())
    )
    health = _build_setup_health(
        manifest=manifest,
        setup=setup,
        profile=profile,
        available_integrations=available_integrations,
    )

    return PluginSetupResponse(
        plugin_name=plugin,
        display_name=manifest.display_name,
        setup=_setup_to_payload(setup),
        health=health,
        updated_at=setup.updated_at if setup else None,
    )


@router.put("/{plugin}/setup", response_model=PluginSetupResponse)
async def upsert_plugin_setup(
    plugin: str,
    body: PluginSetupUpsert,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create or update structured tenant setup for a plugin.

    Admins own tenant-level setup because it controls firm workflow defaults,
    escalation thresholds, and source bindings.
    """
    _validate_plugin(plugin)
    manifest = get_plugin_manifest(plugin)
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    await set_tenant_context(db, str(user.tenant_id))

    setup_result = await db.execute(
        select(TenantPluginSetup).where(
            TenantPluginSetup.tenant_id == user.tenant_id,
            TenantPluginSetup.plugin_name == plugin,
        )
    )
    setup = setup_result.scalar_one_or_none()
    if setup is None:
        setup = TenantPluginSetup(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            plugin_name=plugin,
            created_by_user_id=user.id,
        )
        db.add(setup)

    generated_profile = body.generated_profile or _generate_structured_profile(
        plugin, manifest.display_name, body
    )
    setup.jurisdictions = body.jurisdictions
    setup.escalation_rules = body.escalation_rules
    setup.approval_thresholds = body.approval_thresholds
    setup.template_preferences = body.template_preferences
    setup.cloud_bindings = body.cloud_bindings
    setup.calendar_bindings = body.calendar_bindings
    setup.house_style = body.house_style
    setup.custom_config = body.custom_config
    setup.generated_profile = generated_profile
    setup.is_complete = body.is_complete
    setup.updated_at = datetime.now(timezone.utc)

    profile_result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
            PracticeProfile.plugin_name == plugin,
        )
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        profile = PracticeProfile(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            plugin_name=plugin,
        )
        db.add(profile)
    profile.profile_content = generated_profile
    profile.is_complete = body.is_complete
    profile.updated_at = datetime.now(timezone.utc)

    credential_result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == user.tenant_id,
            TenantCredential.is_active.is_(True),
        )
    )
    available_integrations = _capabilities_from_credentials(
        list(credential_result.scalars().all())
    )
    health = _build_setup_health(
        manifest=manifest,
        setup=setup,
        profile=profile,
        available_integrations=available_integrations,
    )
    setup.setup_health = health.model_dump()

    await db.commit()
    await db.refresh(setup)

    return PluginSetupResponse(
        plugin_name=plugin,
        display_name=manifest.display_name,
        setup=_setup_to_payload(setup),
        health=health,
        updated_at=setup.updated_at,
    )


@router.get("/{plugin}/profile", response_model=PracticeProfileResponse)
async def get_practice_profile(
    plugin: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get the practice profile for a plugin."""
    _validate_plugin(plugin)
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
            PracticeProfile.plugin_name == plugin,
        )
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        return PracticeProfileResponse(
            id="",
            plugin_name=plugin,
            profile_content="",
            is_complete=False,
            setup_step=0,
            updated_at=datetime.now(timezone.utc),
        )

    return PracticeProfileResponse(
        id=str(profile.id),
        plugin_name=profile.plugin_name,
        profile_content=profile.profile_content or "",
        is_complete=profile.is_complete,
        setup_step=profile.setup_step,
        updated_at=profile.updated_at,
    )


@router.put("/{plugin}/profile", response_model=PracticeProfileResponse)
async def upsert_practice_profile(
    plugin: str,
    body: PracticeProfileUpsert,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create or update the practice profile for a plugin."""
    _validate_plugin(plugin)
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
            PracticeProfile.plugin_name == plugin,
        )
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = PracticeProfile(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            plugin_name=plugin,
        )
        db.add(profile)

    profile.profile_content = body.profile_content
    profile.is_complete = body.is_complete
    profile.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(profile)

    return PracticeProfileResponse(
        id=str(profile.id),
        plugin_name=profile.plugin_name,
        profile_content=profile.profile_content or "",
        is_complete=profile.is_complete,
        setup_step=profile.setup_step,
        updated_at=profile.updated_at,
    )


# ── Cold-Start Interview ──────────────────────────────────────────────────────


@router.post("/{plugin}/cold-start", response_model=SkillResponse)
async def cold_start_interview(
    plugin: str,
    body: SkillRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Continue the cold-start setup interview for a plugin."""
    _validate_plugin(plugin)
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # Load current profile/step
    result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
            PracticeProfile.plugin_name == plugin,
        )
    )
    profile = result.scalar_one_or_none()
    current_step = profile.setup_step if profile else 1

    context = body.context or {}
    context["setup_step"] = current_step
    context["tenant_name"] = user.tenant.name if user.tenant else "Legal"

    result_data = await plugin_executor.execute(
        db=db,
        plugin=plugin,
        skill="cold-start-interview",
        input_text=body.input_text,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
        context=context,
        use_premium=body.use_premium,
    )

    # Advance the setup step and persist partial profile
    new_step = min(current_step + 1, 8)
    if profile is None:
        profile = PracticeProfile(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            plugin_name=plugin,
            setup_step=new_step,
        )
        db.add(profile)
    else:
        profile.setup_step = new_step
        profile.updated_at = datetime.now(timezone.utc)

    # If at final step: mark is_complete if no PLACEHOLDERs remain
    if new_step >= 8:
        profile_content = profile.profile_content or ""
        profile.is_complete = "[PLACEHOLDER]" not in profile_content

    # Record usage
    model_used = result_data.get("model_used") or (
        settings.LITELLM_PREMIUM_MODEL
        if body.use_premium
        else settings.LITELLM_STANDARD_MODEL
    )
    tokens_in_val = result_data.get("tokens_in", result_data.get("tokens_used", 0) // 2)
    tokens_out_val = result_data.get(
        "tokens_out", result_data.get("tokens_used", 0) // 2
    )
    cost = calculate_cost(
        tokens_in=tokens_in_val,
        tokens_out=tokens_out_val,
        model=model_used,
        billing_tier=user.tenant.billing_tier if user.tenant else "payg",
    )
    usage = UsageRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        conversation_id=None,
        requested_route=result_data.get("requested_route"),
        resolved_route=result_data.get("resolved_route"),
        gateway_provider=result_data.get("gateway_provider"),
        gateway_alias=result_data.get("gateway_alias"),
        final_model=result_data.get("gateway_alias") or model_used,
        model_used=model_used,
        tokens_in=tokens_in_val,
        tokens_out=tokens_out_val,
        cost_usd=cost,
        operation_type="cold_start",
        query_text=body.input_text[:2000] if body.input_text else None,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
    )
    db.add(usage)

    await db.commit()

    return {
        **result_data,
        "step": new_step,
        "profile_complete": profile.is_complete,
        "profile": profile.profile_content if profile.is_complete else None,
    }


# ── Skill Execution (catch-all — must be LAST) ────────────────────────────────


@router.post("/{plugin}/{skill}", response_model=SkillResponse)
async def execute_skill(
    plugin: str,
    skill: str,
    body: SkillRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Execute a named skill for a plugin."""
    _validate_plugin(plugin)

    # Validate skill exists for this plugin
    manifest = get_plugin_manifest(plugin)
    valid_skills = manifest.skills if manifest else PLUGIN_SKILLS.get(plugin, [])
    if skill not in valid_skills:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Skill '{skill}' not found for plugin '{plugin}'. "
                f"Valid skills: {valid_skills}"
            ),
        )

    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    context = body.context or {}
    tenant_name = user.tenant.name if user.tenant else "Legal"
    context["tenant_name"] = tenant_name
    matter_context = ""
    if body.matter_id:
        (
            matter_context,
            _has_pii,
            pii_findings,
        ) = await matter_context_service.get_safe_matter_context(
            db=db,
            matter_id=body.matter_id,
            privacy_mode=getattr(user, "privacy_mode", False),
        )
        if matter_context:
            context["matter_context"] = matter_context
            context["matter_id"] = body.matter_id
        if pii_findings:
            context["matter_pii_findings"] = pii_findings

    cloud_context = await _build_plugin_cloud_context(
        db=db,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
        tenant_name=tenant_name,
        question=body.input_text,
        matter_context=matter_context,
    )
    if cloud_context:
        existing_matter_context = context.get("matter_context", "")
        context["matter_context"] = (
            f"{existing_matter_context}\n\n--- Cloud Search Results ---\n\n{cloud_context}"
            if existing_matter_context
            else f"--- Cloud Search Results ---\n\n{cloud_context}"
        )

    result_data = await plugin_executor.execute(
        db=db,
        plugin=plugin,
        skill=skill,
        input_text=body.input_text,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
        context=context,
        use_premium=body.use_premium,
    )

    # Record usage
    model_used = result_data.get("model_used") or (
        settings.LITELLM_PREMIUM_MODEL
        if body.use_premium
        else settings.LITELLM_STANDARD_MODEL
    )
    tokens_in_val = result_data.get("tokens_in", result_data.get("tokens_used", 0) // 2)
    tokens_out_val = result_data.get(
        "tokens_out", result_data.get("tokens_used", 0) // 2
    )
    cost = calculate_cost(
        tokens_in=tokens_in_val,
        tokens_out=tokens_out_val,
        model=model_used,
        billing_tier=user.tenant.billing_tier if user.tenant else "payg",
    )
    usage = UsageRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        conversation_id=None,
        requested_route=result_data.get("requested_route"),
        resolved_route=result_data.get("resolved_route"),
        gateway_provider=result_data.get("gateway_provider"),
        gateway_alias=result_data.get("gateway_alias"),
        final_model=result_data.get("gateway_alias") or model_used,
        model_used=model_used,
        tokens_in=tokens_in_val,
        tokens_out=tokens_out_val,
        cost_usd=cost,
        operation_type="plugin_skill",
        query_text=body.input_text[:2000] if body.input_text else None,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
    )
    db.add(usage)
    await db.commit()

    return SkillResponse(**result_data)
