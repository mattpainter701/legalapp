"""
Platform super-admin API.

Authenticated by short-lived, scoped platform bearer tokens. Bootstrap secrets
can only be exchanged at ``/api/platform/auth/token`` and are never accepted by
operational routes.
This is NOT a per-tenant admin endpoint — it has visibility across ALL tenants
and is intended for the SaaS operator only.

Endpoints:
  GET  /api/platform/tenants         — list all tenants with 30-day usage summary
  GET  /api/platform/tenants/{id}    — tenant detail + users + usage
  PUT  /api/platform/tenants/{id}    — update billing_tier / is_active / seat_count
  GET  /api/platform/usage           — aggregate usage across all tenants
  GET  /api/platform/health          — row counts and index info
"""

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import clear_tenant_context, get_db, set_tenant_context
from app.models.conversation import UsageRecord
from app.models.error_log import ErrorLog
from app.models.api_access_log import ApiAccessLog
from app.models.document import Chunk, Document
from app.models.mcp_product import MCPProductKey, MCPUsageEvent
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User
from app.services.mcp_product import DEFAULT_ALLOWED_TOOLS, mask_key
from app.services.llm_routing import (
    VALID_LLM_PROVIDERS,
    default_platform_llm_config,
    get_platform_llm_config,
    upsert_platform_llm_config,
)
from app.services.module_visibility import KNOWN_MODULES, normalize_module_name
from app.services.operator_audit import record_operator_audit
from app.services.durable_jobs import enqueue_job
from app.services.corpus_revision import advance_rag_corpus_revision
from app.services.platform_auth import (
    issue_platform_token,
    require_platform_token,
    verify_platform_bootstrap_key,
)

settings = get_settings()
router = APIRouter(prefix="/platform", tags=["platform"])


# ── Auth ───────────────────────────────────────────────────────────────────────


def _require_platform_key(request: Request) -> None:
    require_platform_token(request)


@asynccontextmanager
async def _platform_tenant_scope(
    db: AsyncSession, tenant_id: uuid.UUID | str
) -> AsyncIterator[None]:
    """Expose exactly one tenant to a verified platform route under RLS.

    Production connects as ``clarity_app`` with ``NOBYPASSRLS``. Platform
    routes therefore enumerate the unscoped tenant registry, then deliberately
    enter one ordinary tenant context at a time. This keeps Postgres RLS active
    and avoids turning the broad auth-only ``app.rls_bypass`` GUC into an
    operator data-access mechanism.
    """

    await set_tenant_context(db, str(tenant_id))
    try:
        yield
    except BaseException:
        # A failed statement can leave PostgreSQL's transaction aborted, in
        # which case a cleanup SELECT would mask the original exception. The
        # request dependency rolls the failed transaction back (and these GUCs
        # are transaction-local), so preserve the real error here.
        try:
            await clear_tenant_context(db)
        except Exception:
            pass
        raise
    else:
        await clear_tenant_context(db)


async def _platform_tenant_ids(
    db: AsyncSession, tenant_id: uuid.UUID | None = None
) -> list[uuid.UUID]:
    """Return registry IDs; the tenants table is intentionally not tenant-RLS data."""

    if tenant_id is not None:
        exists = await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id))
        return [tenant_id] if exists else []
    return list((await db.scalars(select(Tenant.id).order_by(Tenant.id))).all())


async def _platform_paginated_tenant_rows(
    db: AsyncSession,
    model,
    *,
    filters: list,
    page: int,
    limit: int,
    tenant_id: uuid.UUID | None = None,
    include_system_rows: bool = False,
) -> tuple[list, int]:
    """Merge one RLS-scoped page without ever opening cross-tenant visibility."""

    fetch_limit = page * limit
    candidates: list = []
    total = 0

    async def load_visible_rows(tenant_filter) -> None:
        nonlocal total
        scoped_filters = [tenant_filter, *filters]
        total += int(
            await db.scalar(select(func.count(model.id)).where(*scoped_filters)) or 0
        )
        candidates.extend(
            list(
                (
                    await db.scalars(
                        select(model)
                        .where(*scoped_filters)
                        .order_by(model.created_at.desc())
                        .limit(fetch_limit)
                    )
                ).all()
            )
        )

    if include_system_rows and tenant_id is None:
        await clear_tenant_context(db)
        await load_visible_rows(model.tenant_id.is_(None))

    for scoped_tenant_id in await _platform_tenant_ids(db, tenant_id):
        async with _platform_tenant_scope(db, scoped_tenant_id):
            await load_visible_rows(model.tenant_id == scoped_tenant_id)

    candidates.sort(key=lambda row: row.created_at, reverse=True)
    start = (page - 1) * limit
    return candidates[start : start + limit], total


# ── Operator integration readiness ────────────────────────────────────────────


def _setting_configured(key: str) -> bool:
    return bool(getattr(settings, key, ""))


@router.get("/integrations/readiness")
async def platform_integration_readiness(request: Request):
    """Redacted operator-only readiness for shared integration app setup."""
    _require_platform_key(request)

    backend_url = settings.BACKEND_URL.rstrip("/")
    zoom_callback = (
        settings.ZOOM_REDIRECT_URI or f"{backend_url}/api/integrations/zoom/callback"
    )
    client_ready = _setting_configured("ZOOM_CLIENT_ID") and _setting_configured(
        "ZOOM_CLIENT_SECRET"
    )

    return {
        "zoom": {
            "phone_oauth_ready": False,
            "phone_tenant_owned": True,
            "meetings_oauth_ready": client_ready,
            "env": {
                "ZOOM_CLIENT_ID": {
                    "label": "Zoom OAuth client ID",
                    "configured": _setting_configured("ZOOM_CLIENT_ID"),
                    "required": True,
                },
                "ZOOM_CLIENT_SECRET": {
                    "label": "Zoom OAuth client secret",
                    "configured": _setting_configured("ZOOM_CLIENT_SECRET"),
                    "required": True,
                },
                "ZOOM_REDIRECT_URI": {
                    "label": "Meetings callback override",
                    "configured": _setting_configured("ZOOM_REDIRECT_URI"),
                    "required": False,
                },
            },
            "expected_redirect_uris": {
                "zoom_phone": [
                    settings.ZOOM_PHONE_REDIRECT_URI
                    or f"{backend_url}/api/integrations/zoom-phone/callback"
                ],
                "zoom": [zoom_callback],
            },
            "tenant_grant_flow": {
                "phone_provider": "zoom_phone",
                "meetings_provider": "zoom",
                "description": (
                    "Zoom Phone is configured with a tenant-owned app from "
                    "Admin > Zoom. The platform app is Meetings-only."
                ),
            },
            "notes": [
                "Global Zoom client settings apply to Zoom Meetings only.",
                "Each Phone tenant enters its own app credentials and webhook secret in Admin > Zoom.",
                "Never add Zoom Phone scopes or callbacks to the platform Meetings app.",
            ],
        }
    }


# ── Schemas ────────────────────────────────────────────────────────────────────


class TenantSummary(BaseModel):
    id: str
    name: str
    domain: str
    company_name: Optional[str]
    billing_tier: str
    flat_seat_count: int
    is_active: bool
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    stripe_subscription_status: str
    mcp_entitlement_status: str
    mcp_billing_status: str
    user_count: int
    requests_30d: int
    cost_usd_30d: float
    created_at: datetime


class TenantUpdate(BaseModel):
    billing_tier: Optional[str] = None
    is_active: Optional[bool] = None
    seat_count: Optional[int] = None
    llm_provider: Optional[str] = None  # compatibility field; only "litellm" is valid
    llm_model: Optional[str] = None  # optional LiteLLM alias override
    standard_llm_provider: Optional[str] = None
    standard_llm_model: Optional[str] = None
    premium_llm_provider: Optional[str] = None
    premium_llm_model: Optional[str] = None
    enabled_modules: Optional[list[str]] = None
    default_module: Optional[str] = None
    plan: Optional[str] = None
    mcp_entitlement_status: Optional[str] = None
    mcp_billing_status: Optional[str] = None


class PlatformLLMConfigUpdate(BaseModel):
    standard_provider: Optional[str] = None
    standard_model: Optional[str] = None
    premium_provider: Optional[str] = None
    premium_model: Optional[str] = None


class PlatformUsage(BaseModel):
    total_tenants: int
    active_tenants: int
    total_users: int
    requests_30d: int
    cost_usd_30d: float
    period_start: datetime
    period_end: datetime


class PlatformMCPOverview(BaseModel):
    tenants_with_keys: int
    active_keys: int
    total_keys: int
    calls_30d: int
    errors_30d: int
    results_30d: int
    period_start: datetime
    period_end: datetime


class PlatformTokenRequest(BaseModel):
    scopes: list[str] | None = None
    ttl_minutes: int | None = None


class PlatformDocumentReindexRequest(BaseModel):
    tenant_id: uuid.UUID | None = None
    only_degraded: bool = True
    dry_run: bool = False
    limit: int = 100


# ── Helpers ────────────────────────────────────────────────────────────────────


def _mask(val: str | None) -> str | None:
    if not val:
        return None
    return val[:8] + "..." + val[-4:]


def _validate_provider(provider: str | None, field: str = "provider") -> None:
    if provider is not None and provider not in VALID_LLM_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be one of: {', '.join(sorted(VALID_LLM_PROVIDERS))}",
        )


def _field_was_sent(model: BaseModel, field: str) -> bool:
    return field in getattr(model, "model_fields_set", set())


def _validate_modules(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    modules = []
    for value in values:
        module = normalize_module_name(value)
        if not module:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown module '{value}'. Valid modules: {sorted(KNOWN_MODULES)}",
            )
        modules.append(module)
    return modules


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/auth/token")
async def create_platform_session(
    body: PlatformTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Exchange the offline bootstrap secret for a short-lived scoped token."""
    principal = verify_platform_bootstrap_key(request.headers.get("X-Platform-Key", ""))
    token, expires_at, scopes = issue_platform_token(
        subject=principal.operator_id,
        scopes=body.scopes,
        allowed_scopes=principal.scopes,
        ttl_minutes=body.ttl_minutes,
        not_after=principal.expires_at,
    )
    request.state.platform_actor_id = principal.operator_id
    request.state.platform_scope = "platform:bootstrap"
    await record_operator_audit(
        db,
        request,
        action="platform.session.issued",
        resource_type="platform_session",
        actor_type=principal.credential_type,
        actor_id=principal.operator_id,
        metadata={
            "scopes": scopes,
            "expires_at": expires_at.isoformat(),
            "bootstrap_expires_at": principal.expires_at.isoformat(),
        },
    )
    await db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "scopes": scopes,
    }


@router.get("/tenants")
async def list_tenants(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    _require_platform_key(request)

    period_start = datetime.now(timezone.utc) - timedelta(days=30)

    tenants_result = await db.execute(
        select(Tenant)
        .order_by(Tenant.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    tenants = tenants_result.scalars().all()
    user_counts: dict[str, int] = {}
    usage: dict[str, tuple[int, float]] = {}
    for tenant in tenants:
        async with _platform_tenant_scope(db, tenant.id):
            user_counts[str(tenant.id)] = int(
                await db.scalar(
                    select(func.count(User.id)).where(User.tenant_id == tenant.id)
                )
                or 0
            )
            usage_row = (
                await db.execute(
                    select(
                        func.count(UsageRecord.id).label("requests"),
                        func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost"),
                    ).where(
                        UsageRecord.tenant_id == tenant.id,
                        UsageRecord.created_at >= period_start,
                    )
                )
            ).one()
            usage[str(tenant.id)] = (
                int(usage_row.requests or 0),
                float(usage_row.cost or 0),
            )

    total_result = await db.execute(select(func.count(Tenant.id)))
    total = total_result.scalar_one()

    return {
        "tenants": [
            TenantSummary(
                id=str(t.id),
                name=t.name,
                domain=t.domain,
                company_name=t.company_name,
                billing_tier=t.billing_tier,
                flat_seat_count=t.flat_seat_count,
                is_active=t.is_active,
                stripe_customer_id=_mask(t.stripe_customer_id),
                stripe_subscription_id=_mask(t.stripe_subscription_id),
                stripe_subscription_status=t.stripe_subscription_status,
                mcp_entitlement_status=t.mcp_entitlement_status,
                mcp_billing_status=t.mcp_billing_status,
                user_count=user_counts.get(str(t.id), 0),
                requests_30d=usage.get(str(t.id), (0, 0))[0],
                cost_usd_30d=usage.get(str(t.id), (0, 0))[1],
                created_at=t.created_at,
            )
            for t in tenants
        ],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.post("/documents/reindex")
async def reindex_platform_documents(
    body: PlatformDocumentReindexRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Queue a bounded, audited repair of tenant document indexes."""

    _require_platform_key(request)
    if body.limit < 1 or body.limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    tenant_ids = await _platform_tenant_ids(db, body.tenant_id)
    if body.tenant_id is not None and not tenant_ids:
        raise HTTPException(status_code=404, detail="Tenant not found")

    operation_id = uuid.uuid4().hex
    selected: list[dict[str, str]] = []
    skipped_missing_storage: list[dict[str, str]] = []
    job_ids: list[str] = []

    for scoped_tenant_id in tenant_ids:
        remaining = body.limit - len(selected)
        if remaining <= 0:
            break
        tenant_corpus_changed = False
        async with _platform_tenant_scope(db, scoped_tenant_id):
            filters = [Document.tenant_id == scoped_tenant_id]
            if body.only_degraded:
                filters.append(
                    or_(
                        Document.chunk_count == 0,
                        Document.embedding_model.is_(None),
                        Document.status == "error",
                    )
                )
            documents = list(
                (
                    await db.scalars(
                        select(Document)
                        .where(*filters)
                        .order_by(Document.created_at.asc())
                        .limit(remaining)
                    )
                ).all()
            )
            for document in documents:
                storage_path = document.storage_path or ""
                if (
                    not storage_path
                    or storage_path.startswith(("http://", "https://"))
                    or not os.path.exists(storage_path)
                ):
                    skipped_missing_storage.append(
                        {
                            "tenant_id": str(scoped_tenant_id),
                            "document_id": str(document.id),
                            "filename": document.filename,
                        }
                    )
                    continue

                selected.append(
                    {
                        "tenant_id": str(scoped_tenant_id),
                        "document_id": str(document.id),
                        "filename": document.filename,
                    }
                )
                if body.dry_run:
                    continue

                await db.execute(
                    delete(Chunk).where(
                        Chunk.document_id == document.id,
                        Chunk.tenant_id == scoped_tenant_id,
                    )
                )
                document.status = "pending"
                document.chunk_count = 0
                document.error_message = None
                document.indexed_at = None
                document.embedding_model = None
                document.embedding_version = None
                tenant_corpus_changed = True
                job = await enqueue_job(
                    db,
                    tenant_id=scoped_tenant_id,
                    kind="document_ingest",
                    idempotency_key=f"reindex:{document.id}:{operation_id}",
                    payload={"document_id": str(document.id)},
                )
                job_ids.append(str(job.id))
            if not body.dry_run:
                if tenant_corpus_changed:
                    await advance_rag_corpus_revision(db, scoped_tenant_id)
                # Flush tenant-owned rows before clearing the RLS scope.
                await db.flush()

        if not body.dry_run:
            await db.commit()

    await record_operator_audit(
        db,
        request,
        action=(
            "documents.reindex.previewed"
            if body.dry_run
            else "documents.reindex.queued"
        ),
        resource_type="document_index",
        metadata={
            "tenant_id": str(body.tenant_id) if body.tenant_id else None,
            "only_degraded": body.only_degraded,
            "selected_count": len(selected),
            "skipped_missing_storage_count": len(skipped_missing_storage),
            "operation_id": operation_id,
        },
    )
    await db.commit()
    return {
        "dry_run": body.dry_run,
        "selected_count": len(selected),
        "queued_count": 0 if body.dry_run else len(job_ids),
        "documents": selected,
        "job_ids": job_ids,
        "skipped_missing_storage": skipped_missing_storage,
        "operation_id": operation_id,
    }


@router.get("/tenants/{tenant_id}")
async def get_tenant_detail(
    tenant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    period_start = datetime.now(timezone.utc) - timedelta(days=30)
    async with _platform_tenant_scope(db, tenant.id):
        users_result = await db.execute(
            select(User)
            .where(User.tenant_id == tenant.id)
            .order_by(User.created_at.asc())
        )
        users = users_result.scalars().all()

        usage_result = await db.execute(
            select(
                func.count(UsageRecord.id).label("requests"),
                func.coalesce(func.sum(UsageRecord.tokens_in), 0).label("tokens_in"),
                func.coalesce(func.sum(UsageRecord.tokens_out), 0).label("tokens_out"),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost"),
            ).where(
                UsageRecord.tenant_id == tenant.id,
                UsageRecord.created_at >= period_start,
            )
        )
        u = usage_result.one()

        # Fetch tenant settings (LLM provider, etc.)
        ts_result = await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
        )
        ts = ts_result.scalar_one_or_none()

    return {
        "tenant": TenantSummary(
            id=str(tenant.id),
            name=tenant.name,
            domain=tenant.domain,
            company_name=tenant.company_name,
            billing_tier=tenant.billing_tier,
            flat_seat_count=tenant.flat_seat_count,
            is_active=tenant.is_active,
            stripe_customer_id=_mask(tenant.stripe_customer_id),
            stripe_subscription_id=_mask(tenant.stripe_subscription_id),
            stripe_subscription_status=tenant.stripe_subscription_status,
            mcp_entitlement_status=tenant.mcp_entitlement_status,
            mcp_billing_status=tenant.mcp_billing_status,
            user_count=len(users),
            requests_30d=int(u.requests),
            cost_usd_30d=float(u.cost),
            created_at=tenant.created_at,
        ),
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at,
            }
            for u in users
        ],
        "usage_30d": {
            "requests": int(u.requests),
            "tokens_in": int(u.tokens_in),
            "tokens_out": int(u.tokens_out),
            "cost_usd": float(u.cost),
        },
        "llm_config": {
            "provider": ts.default_llm_provider if ts else None,
            "model": ts.default_llm_model if ts else None,
            "standard_provider": ts.default_llm_provider if ts else None,
            "standard_model": ts.default_llm_model if ts else None,
            "premium_provider": ts.premium_llm_provider if ts else None,
            "premium_model": ts.premium_llm_model if ts else None,
        },
        "module_config": {
            "enabled_modules": (ts.custom_config or {}).get("enabled_modules")
            if ts
            else None,
            "default_module": (ts.custom_config or {}).get("default_module")
            if ts
            else None,
            "plan": (ts.custom_config or {}).get("plan") if ts else None,
        },
    }


@router.get("/plans")
async def list_plans(request: Request):
    _require_platform_key(request)
    from app.services.plans import PLANS

    return {
        "plans": [
            {
                "id": p.id,
                "label": p.label,
                "modules": p.modules,
                "public_signup": p.public_signup,
                "upsell_target": p.upsell_target,
            }
            for p in PLANS.values()
        ]
    }


@router.put("/tenants/{tenant_id}")
async def update_tenant(
    tenant_id: str,
    body: TenantUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # All mutable tenant configuration lives behind ordinary tenant RLS. The
    # platform token authorizes selecting this one context; it never enables a
    # cross-tenant bypass.
    await set_tenant_context(db, str(tenant.id))

    audit_changes: dict[str, dict] = {}

    if body.billing_tier is not None:
        if body.billing_tier not in ("flat", "payg"):
            raise HTTPException(
                status_code=400, detail="billing_tier must be 'flat' or 'payg'"
            )
        audit_changes["billing_tier"] = {
            "from": tenant.billing_tier,
            "to": body.billing_tier,
        }
        tenant.billing_tier = body.billing_tier

    if body.is_active is not None:
        audit_changes["is_active"] = {
            "from": tenant.is_active,
            "to": body.is_active,
        }
        tenant.is_active = body.is_active

    if body.mcp_entitlement_status is not None:
        if body.mcp_entitlement_status not in {"disabled", "enabled", "suspended"}:
            raise HTTPException(
                status_code=400, detail="Invalid MCP entitlement status"
            )
        audit_changes["mcp_entitlement_status"] = {
            "from": tenant.mcp_entitlement_status,
            "to": body.mcp_entitlement_status,
        }
        tenant.mcp_entitlement_status = body.mcp_entitlement_status

    if body.mcp_billing_status is not None:
        if body.mcp_billing_status not in {
            "disabled",
            "active",
            "past_due",
            "suspended",
        }:
            raise HTTPException(status_code=400, detail="Invalid MCP billing status")
        audit_changes["mcp_billing_status"] = {
            "from": tenant.mcp_billing_status,
            "to": body.mcp_billing_status,
        }
        tenant.mcp_billing_status = body.mcp_billing_status

    if body.seat_count is not None:
        if body.seat_count < 0:
            raise HTTPException(status_code=400, detail="seat_count must be >= 0")
        audit_changes["seat_count"] = {
            "from": tenant.flat_seat_count,
            "to": body.seat_count,
        }
        tenant.flat_seat_count = body.seat_count

    module_config_sent = _field_was_sent(body, "enabled_modules") or _field_was_sent(
        body, "default_module"
    )
    if module_config_sent:
        enabled_modules = _validate_modules(body.enabled_modules)
        default_module = normalize_module_name(body.default_module)
        if _field_was_sent(body, "default_module") and not default_module:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown default_module '{body.default_module}'. Valid modules: {sorted(KNOWN_MODULES)}",
            )
        if (
            enabled_modules is not None
            and default_module
            and default_module not in enabled_modules
        ):
            raise HTTPException(
                status_code=400,
                detail="default_module must be included in enabled_modules",
            )

        ts_result = await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
        )
        ts = ts_result.scalar_one_or_none()
        if ts is None:
            ts = TenantSettings(tenant_id=tenant.id)
            db.add(ts)
            await db.flush()
        custom_config = dict(ts.custom_config or {})
        if enabled_modules is not None:
            audit_changes["enabled_modules"] = {
                "from": custom_config.get("enabled_modules"),
                "to": enabled_modules,
            }
            custom_config["enabled_modules"] = enabled_modules
        if default_module is not None:
            audit_changes["default_module"] = {
                "from": custom_config.get("default_module"),
                "to": default_module,
            }
            custom_config["default_module"] = default_module
        ts.custom_config = custom_config

    if _field_was_sent(body, "plan"):
        from app.services.plans import get_plan

        plan_value = body.plan or None
        if plan_value is not None and get_plan(plan_value) is None:
            raise HTTPException(status_code=400, detail=f"Unknown plan '{body.plan}'")
        ts_result = await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
        )
        ts = ts_result.scalar_one_or_none()
        if ts is None:
            ts = TenantSettings(tenant_id=tenant.id)
            db.add(ts)
            await db.flush()
        custom_config = dict(ts.custom_config or {})
        audit_changes["plan"] = {
            "from": custom_config.get("plan"),
            "to": plan_value,
        }
        if plan_value is None:
            custom_config.pop("plan", None)
        else:
            custom_config["plan"] = plan_value
        ts.custom_config = custom_config

    standard_provider_sent = _field_was_sent(
        body, "standard_llm_provider"
    ) or _field_was_sent(body, "llm_provider")
    standard_model_sent = _field_was_sent(
        body, "standard_llm_model"
    ) or _field_was_sent(body, "llm_model")
    premium_provider_sent = _field_was_sent(body, "premium_llm_provider")
    premium_model_sent = _field_was_sent(body, "premium_llm_model")

    # LLM routing — stored on TenantSettings
    if (
        standard_provider_sent
        or standard_model_sent
        or premium_provider_sent
        or premium_model_sent
    ):
        standard_provider = (
            body.standard_llm_provider
            if _field_was_sent(body, "standard_llm_provider")
            else body.llm_provider
        )
        standard_model = (
            body.standard_llm_model
            if _field_was_sent(body, "standard_llm_model")
            else body.llm_model
        )
        _validate_provider(standard_provider, "standard_llm_provider")
        _validate_provider(body.premium_llm_provider, "premium_llm_provider")

        platform_llm = await get_platform_llm_config(db)
        active_aliases = {
            str(platform_llm.get("standard_model") or "").strip(),
            str(platform_llm.get("premium_model") or "").strip(),
        } - {""}
        requested_aliases = {
            value
            for value in (standard_model, body.premium_llm_model)
            if value is not None
        }
        unknown_aliases = requested_aliases - active_aliases
        if unknown_aliases:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Tenant routes must use an active platform alias. Unknown: "
                    + ", ".join(sorted(unknown_aliases))
                ),
            )

        ts_result = await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
        )
        ts = ts_result.scalar_one_or_none()
        if not ts:
            ts = TenantSettings(tenant_id=tenant.id)
            db.add(ts)
        if standard_provider_sent:
            audit_changes["standard_llm_provider"] = {
                "from": ts.default_llm_provider,
                "to": standard_provider,
            }
            ts.default_llm_provider = standard_provider
        if standard_model_sent:
            audit_changes["standard_llm_model"] = {
                "from": ts.default_llm_model,
                "to": standard_model,
            }
            ts.default_llm_model = standard_model
        if premium_provider_sent:
            audit_changes["premium_llm_provider"] = {
                "from": ts.premium_llm_provider,
                "to": body.premium_llm_provider,
            }
            ts.premium_llm_provider = body.premium_llm_provider
        if premium_model_sent:
            audit_changes["premium_llm_model"] = {
                "from": ts.premium_llm_model,
                "to": body.premium_llm_model,
            }
            ts.premium_llm_model = body.premium_llm_model

    if audit_changes:
        await record_operator_audit(
            db,
            request,
            action="tenant.updated",
            resource_type="tenant",
            resource_id=str(tenant.id),
            metadata={
                "tenant_id": str(tenant.id),
                "tenant_name": tenant.name,
                "changes": audit_changes,
            },
        )

    # Flush RLS-protected settings while the selected tenant context is still
    # active. ``commit()`` flushes pending ORM state, so clearing first without
    # this explicit flush can make a late TenantSettings INSERT fail closed.
    await db.flush()
    await clear_tenant_context(db)
    await db.commit()
    return {"status": "updated", "tenant_id": tenant_id}


@router.get("/usage", response_model=PlatformUsage)
async def platform_usage(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)

    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)

    tenant_counts = await db.execute(
        select(
            func.count(Tenant.id).label("total"),
            func.count(Tenant.id).filter(Tenant.is_active).label("active"),
        )
    )
    tc = tenant_counts.one()

    tenant_ids = list((await db.scalars(select(Tenant.id).order_by(Tenant.id))).all())
    total_users = 0
    total_requests = 0
    total_cost = 0.0
    for tenant_id in tenant_ids:
        async with _platform_tenant_scope(db, tenant_id):
            total_users += int(
                await db.scalar(
                    select(func.count(User.id)).where(User.tenant_id == tenant_id)
                )
                or 0
            )
            usage_row = (
                await db.execute(
                    select(
                        func.count(UsageRecord.id).label("requests"),
                        func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost"),
                    ).where(
                        UsageRecord.tenant_id == tenant_id,
                        UsageRecord.created_at >= period_start,
                    )
                )
            ).one()
            total_requests += int(usage_row.requests or 0)
            total_cost += float(usage_row.cost or 0)

    return PlatformUsage(
        total_tenants=int(tc.total),
        active_tenants=int(tc.active),
        total_users=total_users,
        requests_30d=total_requests,
        cost_usd_30d=total_cost,
        period_start=period_start,
        period_end=period_end,
    )


@router.get("/mcp")
async def platform_mcp_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)

    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)

    tenants = list((await db.scalars(select(Tenant).order_by(Tenant.id))).all())
    keys: list[tuple[MCPProductKey, Tenant]] = []
    usage_by_key: dict[str, dict] = {}
    for tenant in tenants:
        async with _platform_tenant_scope(db, tenant.id):
            tenant_keys = list(
                (
                    await db.scalars(
                        select(MCPProductKey)
                        .where(MCPProductKey.tenant_id == tenant.id)
                        .order_by(MCPProductKey.created_at.desc())
                    )
                ).all()
            )
            keys.extend((key, tenant) for key in tenant_keys)
            key_ids = [key.id for key in tenant_keys]
            if not key_ids:
                continue
            usage_rows = await db.execute(
                select(
                    MCPUsageEvent.product_key_id,
                    func.count(MCPUsageEvent.id).label("calls"),
                    func.coalesce(func.sum(MCPUsageEvent.result_count), 0).label(
                        "results"
                    ),
                    func.count(MCPUsageEvent.id)
                    .filter(MCPUsageEvent.status_code >= 400)
                    .label("errors"),
                    func.max(MCPUsageEvent.created_at).label("last_call_at"),
                )
                .where(
                    MCPUsageEvent.tenant_id == tenant.id,
                    MCPUsageEvent.product_key_id.in_(key_ids),
                    MCPUsageEvent.created_at >= period_start,
                )
                .group_by(MCPUsageEvent.product_key_id)
            )
            for row in usage_rows.all():
                usage_by_key[str(row.product_key_id)] = {
                    "calls_30d": int(row.calls or 0),
                    "results_30d": int(row.results or 0),
                    "errors_30d": int(row.errors or 0),
                    "last_call_at": row.last_call_at.isoformat()
                    if row.last_call_at
                    else None,
                }
    keys.sort(
        key=lambda pair: pair[0].created_at.timestamp() if pair[0].created_at else 0,
        reverse=True,
    )

    tenant_summary: dict[str, dict] = {}
    key_payload = []
    for key, tenant in keys:
        tenant_id = str(tenant.id)
        usage = usage_by_key.get(str(key.id), {})
        calls = int(usage.get("calls_30d") or 0)
        errors = int(usage.get("errors_30d") or 0)
        results = int(usage.get("results_30d") or 0)
        tenant_row = tenant_summary.setdefault(
            tenant_id,
            {
                "tenant_id": tenant_id,
                "tenant_name": tenant.name,
                "domain": tenant.domain,
                "billing_tier": tenant.billing_tier,
                "stripe_customer_id": _mask(tenant.stripe_customer_id),
                "active_keys": 0,
                "total_keys": 0,
                "calls_30d": 0,
                "errors_30d": 0,
                "results_30d": 0,
                "last_used_at": None,
            },
        )
        tenant_row["total_keys"] += 1
        tenant_row["calls_30d"] += calls
        tenant_row["errors_30d"] += errors
        tenant_row["results_30d"] += results
        if key.is_active and key.revoked_at is None:
            tenant_row["active_keys"] += 1
        last_used = key.last_used_at.isoformat() if key.last_used_at else None
        if last_used and (
            tenant_row["last_used_at"] is None or last_used > tenant_row["last_used_at"]
        ):
            tenant_row["last_used_at"] = last_used

        key_payload.append(
            {
                "id": str(key.id),
                "tenant_id": tenant_id,
                "tenant_name": tenant.name,
                "domain": tenant.domain,
                "name": key.name,
                "api_key_masked": mask_key(key.key_prefix, key.key_hash[-4:]),
                "allowed_tools": key.allowed_tools or DEFAULT_ALLOWED_TOOLS,
                "monthly_call_limit": key.monthly_call_limit,
                "is_active": key.is_active and key.revoked_at is None,
                "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
                "last_used_at": last_used,
                "created_at": key.created_at.isoformat() if key.created_at else None,
                "billing": {
                    "mode": "metered_usage",
                    "meter": "mcp_product_key_calls",
                    "line_item": "MCP usage",
                    "stripe_customer_id": _mask(tenant.stripe_customer_id),
                },
                **usage,
            }
        )

    overview = PlatformMCPOverview(
        tenants_with_keys=len(tenant_summary),
        active_keys=sum(
            1 for key, _tenant in keys if key.is_active and key.revoked_at is None
        ),
        total_keys=len(keys),
        calls_30d=sum(row.get("calls_30d", 0) for row in usage_by_key.values()),
        errors_30d=sum(row.get("errors_30d", 0) for row in usage_by_key.values()),
        results_30d=sum(row.get("results_30d", 0) for row in usage_by_key.values()),
        period_start=period_start,
        period_end=period_end,
    )

    return {
        "overview": overview,
        "tenants": sorted(
            tenant_summary.values(),
            key=lambda row: (row["calls_30d"], row["active_keys"]),
            reverse=True,
        ),
        "keys": key_payload,
        "connection": {
            "server_url": f"{settings.BACKEND_URL.rstrip('/')}/api/mcp",
            "streamable_http": f"{settings.BACKEND_URL.rstrip('/')}/api/mcp",
            "rest_compatibility": f"{settings.BACKEND_URL.rstrip('/')}/api/mcp/tools/call",
            "auth_header": "X-MCP-API-Key",
        },
        "product_enabled": settings.MCP_PRODUCT_ENABLED,
    }


@router.get("/llm-config")
async def get_llm_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get live platform-wide standard/premium LLM routing."""
    _require_platform_key(request)
    return {"config": await get_platform_llm_config(db)}


@router.put("/llm-config")
async def update_llm_config(
    body: PlatformLLMConfigUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update live platform-wide standard/premium LLM routing."""
    _require_platform_key(request)

    _validate_provider(body.standard_provider, "standard_provider")
    _validate_provider(body.premium_provider, "premium_provider")

    updates = {
        key: getattr(body, key)
        for key in default_platform_llm_config()
        if _field_was_sent(body, key)
    }
    config = await upsert_platform_llm_config(db, updates)
    await db.commit()
    return {"status": "updated", "config": config}


async def _fetch_openai_compatible_models(
    *,
    base_url: str,
    api_key: str,
) -> list[str]:
    if not base_url or not api_key:
        return []
    import httpx

    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        item.get("id")
        for item in data.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]


async def _safe_models(coro, fallback: list[str]) -> list[str]:
    try:
        models = await coro
        deduped = list(dict.fromkeys(m for m in models if m))
        return deduped or fallback
    except Exception:
        return fallback


@router.get("/llm-providers")
async def list_llm_providers(request: Request):
    """List the LiteLLM gateway and configured aliases."""
    _require_platform_key(request)

    def _provider(key: str, label: str, free_tier: bool, models: list[str]) -> dict:
        configured = bool(settings.LITELLM_ENABLED or settings.LITELLM_API_KEY)
        return {
            "key": key,
            "label": label,
            "configured": configured,
            "free_tier": free_tier,
            "models": models,
        }

    litellm_models = await _safe_models(
        _fetch_openai_compatible_models(
            base_url=settings.LITELLM_BASE_URL,
            api_key=(
                settings.LITELLM_API_KEY
                or ("not-needed" if settings.LITELLM_ENABLED else "")
            ),
        ),
        [settings.LITELLM_STANDARD_MODEL, settings.LITELLM_PREMIUM_MODEL],
    )

    providers = [
        _provider("litellm", "LiteLLM Gateway", False, litellm_models),
    ]

    return {"providers": providers}


@router.get("/health")
async def platform_health(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)

    rows = await db.execute(
        text("""
            SELECT
                relname AS table_name,
                n_live_tup AS row_count,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size
            FROM pg_stat_user_tables
            ORDER BY n_live_tup DESC
        """)
    )
    tables = [
        {"table": r.table_name, "rows": r.row_count, "size": r.total_size}
        for r in rows.fetchall()
    ]

    # Upstream provider health belongs to LiteLLM; the app reports gateway status.
    services = [
        {
            "name": "PostgreSQL",
            "online": len(tables) > 0,
        },
        {
            "name": "Redis",
            "online": bool(settings.REDIS_URL),
        },
        {
            "name": "API Server",
            "online": True,
        },
        {
            "name": "LiteLLM Gateway",
            "online": bool(settings.LITELLM_ENABLED or settings.LITELLM_API_KEY),
        },
    ]

    return {
        "tables": tables,
        "services": services,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Platform Log Schemas ──────────────────────────────────────────────────────


class PlatformErrorEntry(BaseModel):
    id: str
    tenant_id: str
    tenant_name: Optional[str] = None
    user_id: Optional[str] = None
    error_type: str
    severity: str
    message: str
    endpoint: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    is_resolved: bool
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    created_at: datetime


class PlatformErrorList(BaseModel):
    errors: list[PlatformErrorEntry]
    total: int
    page: int
    limit: int


class PlatformErrorSummary(BaseModel):
    total_errors: int
    unresolved: int
    by_severity: dict[str, int]
    by_type: dict[str, int]
    by_tenant: list[dict]
    trend: list[dict]
    days: int


class TenantErrorSummary(BaseModel):
    total_errors: int
    unresolved: int
    by_severity: dict[str, int]
    by_type: dict[str, int]
    trend: list[dict]
    days: int


# ── Platform Log Endpoints ────────────────────────────────────────────────────


@router.get("/logs", response_model=PlatformErrorList)
async def list_platform_errors(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None, pattern="^(critical|error|warning|info)$"),
    error_type: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=90),
    unresolved_only: bool = Query(False),
):
    _require_platform_key(request)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    filters = [ErrorLog.created_at >= cutoff]
    tid: uuid.UUID | None = None
    if severity:
        filters.append(ErrorLog.severity == severity)
    if error_type:
        filters.append(ErrorLog.error_type == error_type)
    if tenant_id:
        try:
            tid = (
                uuid.UUID(tenant_id)
                if not isinstance(tenant_id, uuid.UUID)
                else tenant_id
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")
    if unresolved_only:
        filters.append(ErrorLog.is_resolved.is_(False))

    errors, total = await _platform_paginated_tenant_rows(
        db,
        ErrorLog,
        filters=filters,
        page=page,
        limit=limit,
        tenant_id=tid,
        include_system_rows=True,
    )

    # Resolve tenant names
    tids = {e.tenant_id for e in errors}
    tenant_names: dict[uuid.UUID, str] = {}
    if tids:
        tn_result = await db.execute(
            select(Tenant.id, Tenant.name).where(Tenant.id.in_(tids))
        )
        tenant_names = {str(r.id): r.name for r in tn_result.fetchall()}

    return PlatformErrorList(
        errors=[
            PlatformErrorEntry(
                id=str(e.id),
                tenant_id=str(e.tenant_id),
                tenant_name=tenant_names.get(str(e.tenant_id), "—"),
                user_id=str(e.user_id)[:8] + "…" if e.user_id else None,
                error_type=e.error_type,
                severity=e.severity,
                message=e.message,
                endpoint=e.endpoint,
                method=e.method,
                status_code=e.status_code,
                is_resolved=e.is_resolved,
                resolved_at=e.resolved_at,
                resolution_notes=e.resolution_notes,
                created_at=e.created_at,
            )
            for e in errors
        ],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/logs/summary", response_model=PlatformErrorSummary)
async def platform_error_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90),
):
    _require_platform_key(request)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total_errors = 0
    unresolved = 0
    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    counts_by_tenant: dict[uuid.UUID | None, int] = {}
    trend_map: dict[str, dict[str, int]] = {}

    async def aggregate_visible_errors(tenant_filter, tenant_key) -> None:
        nonlocal total_errors, unresolved
        scoped_filters = [tenant_filter, ErrorLog.created_at >= cutoff]
        totals = (
            await db.execute(
                select(
                    func.count(ErrorLog.id).label("total"),
                    func.count(ErrorLog.id)
                    .filter(ErrorLog.is_resolved.is_(False))
                    .label("unresolved"),
                ).where(*scoped_filters)
            )
        ).one()
        visible_total = int(totals.total or 0)
        total_errors += visible_total
        unresolved += int(totals.unresolved or 0)
        if visible_total:
            counts_by_tenant[tenant_key] = visible_total

        severity_rows = await db.execute(
            select(
                ErrorLog.severity,
                func.count(ErrorLog.id).label("cnt"),
            )
            .where(*scoped_filters)
            .group_by(ErrorLog.severity)
        )
        for row in severity_rows.all():
            by_severity[row.severity] = by_severity.get(row.severity, 0) + int(row.cnt)

        type_rows = await db.execute(
            select(
                ErrorLog.error_type,
                func.count(ErrorLog.id).label("cnt"),
            )
            .where(*scoped_filters)
            .group_by(ErrorLog.error_type)
        )
        for row in type_rows.all():
            by_type[row.error_type] = by_type.get(row.error_type, 0) + int(row.cnt)

        trend_rows = await db.execute(
            select(
                func.date(ErrorLog.created_at).label("day"),
                ErrorLog.severity,
                func.count(ErrorLog.id).label("cnt"),
            )
            .where(*scoped_filters)
            .group_by(func.date(ErrorLog.created_at), ErrorLog.severity)
        )
        for row in trend_rows.all():
            day = str(row.day)
            counts = trend_map.setdefault(
                day, {"critical": 0, "error": 0, "warning": 0, "info": 0}
            )
            counts[row.severity] = counts.get(row.severity, 0) + int(row.cnt)

    await clear_tenant_context(db)
    await aggregate_visible_errors(ErrorLog.tenant_id.is_(None), None)
    for scoped_tenant_id in await _platform_tenant_ids(db):
        async with _platform_tenant_scope(db, scoped_tenant_id):
            await aggregate_visible_errors(
                ErrorLog.tenant_id == scoped_tenant_id, scoped_tenant_id
            )

    tenant_ids = [key for key in counts_by_tenant if key is not None]
    tenant_names = {
        str(row.id): row.name
        for row in (
            await db.execute(
                select(Tenant.id, Tenant.name).where(Tenant.id.in_(tenant_ids))
            )
        ).all()
    }
    by_tenant = [
        {
            "tenant_id": str(tenant_key),
            "tenant_name": "System"
            if tenant_key is None
            else tenant_names.get(str(tenant_key), "—"),
            "count": count,
        }
        for tenant_key, count in sorted(
            counts_by_tenant.items(), key=lambda item: item[1], reverse=True
        )[:20]
    ]

    trend = [
        {
            "date": day,
            "total": sum(counts.values()),
            "critical": counts["critical"],
            "error": counts["error"],
            "warning": counts["warning"],
            "info": counts["info"],
        }
        for day, counts in sorted(trend_map.items())
    ]

    return PlatformErrorSummary(
        total_errors=total_errors,
        unresolved=unresolved,
        by_severity=by_severity,
        by_type=by_type,
        by_tenant=by_tenant,
        trend=trend,
        days=days,
    )


@router.get("/logs/tenant/{tenant_id}", response_model=PlatformErrorList)
async def tenant_error_logs(
    tenant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None, pattern="^(critical|error|warning|info)$"),
    error_type: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=90),
    unresolved_only: bool = Query(False),
):
    _require_platform_key(request)

    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")
    if not await _platform_tenant_ids(db, tid):
        raise HTTPException(status_code=404, detail="Tenant not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    filters = [ErrorLog.created_at >= cutoff]
    if severity:
        filters.append(ErrorLog.severity == severity)
    if error_type:
        filters.append(ErrorLog.error_type == error_type)
    if unresolved_only:
        filters.append(ErrorLog.is_resolved.is_(False))

    errors, total = await _platform_paginated_tenant_rows(
        db,
        ErrorLog,
        filters=filters,
        page=page,
        limit=limit,
        tenant_id=tid,
    )

    # Get tenant name
    tn_result = await db.execute(select(Tenant.name).where(Tenant.id == tid))
    tname = tn_result.scalar_one_or_none() or "—"

    return PlatformErrorList(
        errors=[
            PlatformErrorEntry(
                id=str(e.id),
                tenant_id=str(e.tenant_id),
                tenant_name=tname,
                user_id=str(e.user_id)[:8] + "…" if e.user_id else None,
                error_type=e.error_type,
                severity=e.severity,
                message=e.message,
                endpoint=e.endpoint,
                method=e.method,
                status_code=e.status_code,
                is_resolved=e.is_resolved,
                resolved_at=e.resolved_at,
                resolution_notes=e.resolution_notes,
                created_at=e.created_at,
            )
            for e in errors
        ],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/logs/tenant/{tenant_id}/summary", response_model=TenantErrorSummary)
async def tenant_error_summary(
    tenant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90),
):
    _require_platform_key(request)

    try:
        tid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")
    if not await _platform_tenant_ids(db, tid):
        raise HTTPException(status_code=404, detail="Tenant not found")
    await set_tenant_context(db, str(tid))

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base_filters = [ErrorLog.tenant_id == tid, ErrorLog.created_at >= cutoff]

    total_result = await db.execute(
        select(func.count(ErrorLog.id)).where(*base_filters)
    )
    total_errors = total_result.scalar_one()

    unresolved_result = await db.execute(
        select(func.count(ErrorLog.id)).where(
            *base_filters, ErrorLog.is_resolved.is_(False)
        )
    )
    unresolved = unresolved_result.scalar_one()

    sev_result = await db.execute(
        select(ErrorLog.severity, func.count(ErrorLog.id))
        .where(*base_filters)
        .group_by(ErrorLog.severity)
    )
    by_severity = {row.severity: row.count for row in sev_result.all()}

    type_result = await db.execute(
        select(ErrorLog.error_type, func.count(ErrorLog.id))
        .where(*base_filters)
        .group_by(ErrorLog.error_type)
    )
    by_type = {row.error_type: row.count for row in type_result.all()}

    trend_result = await db.execute(
        select(
            func.date(ErrorLog.created_at).label("day"),
            ErrorLog.severity,
            func.count(ErrorLog.id).label("cnt"),
        )
        .where(*base_filters)
        .group_by(func.date(ErrorLog.created_at), ErrorLog.severity)
        .order_by(func.date(ErrorLog.created_at))
    )
    trend_map: dict = {}
    for row in trend_result.all():
        day_str = str(row.day)
        if day_str not in trend_map:
            trend_map[day_str] = {"critical": 0, "error": 0, "warning": 0, "info": 0}
        trend_map[day_str][row.severity] = row.cnt

    trend = [
        {
            "date": day,
            "total": sum(counts.values()),
            "critical": counts["critical"],
            "error": counts["error"],
            "warning": counts["warning"],
            "info": counts["info"],
        }
        for day, counts in sorted(trend_map.items())
    ]

    result = TenantErrorSummary(
        total_errors=total_errors,
        unresolved=unresolved,
        by_severity=by_severity,
        by_type=by_type,
        trend=trend,
        days=days,
    )
    await clear_tenant_context(db)
    return result


# ── API Access Log Schemas ───────────────────────────────────────────────────


class AccessLogEntry(BaseModel):
    id: str
    tenant_id: str
    tenant_name: Optional[str] = None
    endpoint: str
    method: str
    status_code: int
    latency_ms: Optional[float] = None
    created_at: datetime


class AccessLogList(BaseModel):
    entries: list[AccessLogEntry]
    total: int
    page: int
    limit: int


class AccessLogSummary(BaseModel):
    total_requests: int
    by_status: dict[str, int]
    avg_latency_ms: Optional[float] = None
    by_endpoint: list[dict]
    by_tenant: list[dict]
    days: int


# ── Access Log Endpoints ────────────────────────────────────────────────────


@router.get("/access-logs", response_model=AccessLogList)
async def list_access_logs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    tenant_id: Optional[str] = Query(None),
    endpoint: Optional[str] = Query(None),
    status_code: Optional[int] = Query(None),
    hours: int = Query(24, ge=1, le=168),
):
    _require_platform_key(request)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    filters = [ApiAccessLog.created_at >= cutoff]
    tid: uuid.UUID | None = None
    if tenant_id:
        try:
            tid = uuid.UUID(tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")
    if endpoint:
        filters.append(ApiAccessLog.endpoint.ilike(f"%{endpoint}%"))
    if status_code is not None:
        filters.append(ApiAccessLog.status_code == status_code)

    entries, total = await _platform_paginated_tenant_rows(
        db,
        ApiAccessLog,
        filters=filters,
        page=page,
        limit=limit,
        tenant_id=tid,
    )

    tids = {e.tenant_id for e in entries}
    tn_map: dict[uuid.UUID, str] = {}
    if tids:
        tn_r = await db.execute(
            select(Tenant.id, Tenant.name).where(Tenant.id.in_(tids))
        )
        tn_map = {str(r.id): r.name for r in tn_r.fetchall()}

    return AccessLogList(
        entries=[
            AccessLogEntry(
                id=str(e.id),
                tenant_id=str(e.tenant_id),
                tenant_name=tn_map.get(str(e.tenant_id), "—"),
                endpoint=e.endpoint,
                method=e.method,
                status_code=e.status_code,
                latency_ms=e.latency_ms,
                created_at=e.created_at,
            )
            for e in entries
        ],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/access-logs/summary", response_model=AccessLogSummary)
async def access_log_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=168),
):
    _require_platform_key(request)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    tid: uuid.UUID | None = None
    if tenant_id:
        try:
            tid = uuid.UUID(tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")
    scoped_tenant_ids = await _platform_tenant_ids(db, tid)
    if tid is not None and not scoped_tenant_ids:
        raise HTTPException(status_code=404, detail="Tenant not found")

    total_requests = 0
    latency_sum = 0.0
    latency_count = 0
    by_status: dict[str, int] = {}
    endpoint_counts: dict[str, int] = {}
    tenant_counts: dict[uuid.UUID, int] = {}

    for scoped_tenant_id in scoped_tenant_ids:
        async with _platform_tenant_scope(db, scoped_tenant_id):
            filters = [
                ApiAccessLog.tenant_id == scoped_tenant_id,
                ApiAccessLog.created_at >= cutoff,
            ]
            totals = (
                await db.execute(
                    select(
                        func.count(ApiAccessLog.id).label("total"),
                        func.count(ApiAccessLog.latency_ms).label("latency_count"),
                        func.coalesce(func.sum(ApiAccessLog.latency_ms), 0).label(
                            "latency_sum"
                        ),
                    ).where(*filters)
                )
            ).one()
            visible_total = int(totals.total or 0)
            total_requests += visible_total
            latency_count += int(totals.latency_count or 0)
            latency_sum += float(totals.latency_sum or 0)
            if visible_total:
                tenant_counts[scoped_tenant_id] = visible_total

            status_rows = await db.execute(
                select(
                    ApiAccessLog.status_code,
                    func.count(ApiAccessLog.id).label("cnt"),
                )
                .where(*filters)
                .group_by(ApiAccessLog.status_code)
            )
            for row in status_rows.all():
                key = str(row.status_code)
                by_status[key] = by_status.get(key, 0) + int(row.cnt)

            endpoint_rows = await db.execute(
                select(
                    ApiAccessLog.endpoint,
                    func.count(ApiAccessLog.id).label("cnt"),
                )
                .where(*filters)
                .group_by(ApiAccessLog.endpoint)
            )
            for row in endpoint_rows.all():
                endpoint_counts[row.endpoint] = endpoint_counts.get(
                    row.endpoint, 0
                ) + int(row.cnt)

    by_endpoint = [
        {"endpoint": endpoint, "count": count}
        for endpoint, count in sorted(
            endpoint_counts.items(), key=lambda item: item[1], reverse=True
        )[:20]
    ]

    tenant_names = {
        str(row.id): row.name
        for row in (
            await db.execute(
                select(Tenant.id, Tenant.name).where(Tenant.id.in_(tenant_counts))
            )
        ).all()
    }
    by_tenant = [
        {
            "tenant_id": str(scoped_tenant_id),
            "tenant_name": tenant_names.get(str(scoped_tenant_id), "—"),
            "count": count,
        }
        for scoped_tenant_id, count in sorted(
            tenant_counts.items(), key=lambda item: item[1], reverse=True
        )[:20]
    ]

    avg_latency_ms = latency_sum / latency_count if latency_count else None

    return AccessLogSummary(
        total_requests=total_requests,
        by_status=by_status,
        avg_latency_ms=round(float(avg_latency_ms), 2) if avg_latency_ms else None,
        by_endpoint=by_endpoint,
        by_tenant=by_tenant,
        days=hours // 24 or 1,
    )
