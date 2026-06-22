"""
Platform super-admin API.

Authenticated by X-Platform-Key header matching settings.PLATFORM_SECRET_KEY.
This is NOT a per-tenant admin endpoint — it has visibility across ALL tenants
and is intended for the SaaS operator only.

Endpoints:
  GET  /api/platform/tenants         — list all tenants with 30-day usage summary
  GET  /api/platform/tenants/{id}    — tenant detail + users + usage
  PUT  /api/platform/tenants/{id}    — update billing_tier / is_active / seat_count
  GET  /api/platform/usage           — aggregate usage across all tenants
  GET  /api/platform/health          — row counts and index info
"""

import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.conversation import UsageRecord
from app.models.error_log import ErrorLog
from app.models.api_access_log import ApiAccessLog
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User
from app.services.llm_routing import (
    VALID_LLM_PROVIDERS,
    default_platform_llm_config,
    get_platform_llm_config,
    upsert_platform_llm_config,
)
from app.services.module_visibility import KNOWN_MODULES, normalize_module_name

settings = get_settings()
router = APIRouter(prefix="/platform", tags=["platform"])


# ── Auth ───────────────────────────────────────────────────────────────────────


def _require_platform_key(request: Request) -> None:
    key = request.headers.get("X-Platform-Key", "")
    secret = settings.PLATFORM_SECRET_KEY
    if not secret or len(secret) < 32 or not hmac.compare_digest(key, secret):
        raise HTTPException(status_code=403, detail="Invalid or missing platform key")


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
    tenant_ids = [t.id for t in tenants]

    # User counts per tenant
    user_counts_result = await db.execute(
        select(User.tenant_id, func.count(User.id).label("cnt"))
        .where(User.tenant_id.in_(tenant_ids))
        .group_by(User.tenant_id)
    )
    user_counts = {str(r.tenant_id): r.cnt for r in user_counts_result.fetchall()}

    # 30-day usage per tenant
    usage_result = await db.execute(
        select(
            UsageRecord.tenant_id,
            func.count(UsageRecord.id).label("requests"),
            func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost"),
        )
        .where(
            UsageRecord.tenant_id.in_(tenant_ids),
            UsageRecord.created_at >= period_start,
        )
        .group_by(UsageRecord.tenant_id)
    )
    usage = {
        str(r.tenant_id): (r.requests, float(r.cost)) for r in usage_result.fetchall()
    }

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

    users_result = await db.execute(
        select(User).where(User.tenant_id == tenant.id).order_by(User.created_at.asc())
    )
    users = users_result.scalars().all()

    period_start = datetime.now(timezone.utc) - timedelta(days=30)
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

    if body.billing_tier is not None:
        if body.billing_tier not in ("flat", "payg"):
            raise HTTPException(
                status_code=400, detail="billing_tier must be 'flat' or 'payg'"
            )
        tenant.billing_tier = body.billing_tier

    if body.is_active is not None:
        tenant.is_active = body.is_active

    if body.seat_count is not None:
        if body.seat_count < 0:
            raise HTTPException(status_code=400, detail="seat_count must be >= 0")
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
            custom_config["enabled_modules"] = enabled_modules
        if default_module is not None:
            custom_config["default_module"] = default_module
        ts.custom_config = custom_config

    if _field_was_sent(body, "plan"):
        from app.services.plans import get_plan

        if get_plan(body.plan) is None:
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
        custom_config["plan"] = body.plan
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

        ts_result = await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
        )
        ts = ts_result.scalar_one_or_none()
        if not ts:
            ts = TenantSettings(tenant_id=tenant.id)
            db.add(ts)
        if standard_provider_sent:
            ts.default_llm_provider = standard_provider
        if standard_model_sent:
            ts.default_llm_model = standard_model
        if premium_provider_sent:
            ts.premium_llm_provider = body.premium_llm_provider
        if premium_model_sent:
            ts.premium_llm_model = body.premium_llm_model

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

    user_count = await db.execute(select(func.count(User.id)))

    usage = await db.execute(
        select(
            func.count(UsageRecord.id).label("requests"),
            func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost"),
        ).where(UsageRecord.created_at >= period_start)
    )
    u = usage.one()

    return PlatformUsage(
        total_tenants=int(tc.total),
        active_tenants=int(tc.active),
        total_users=int(user_count.scalar_one()),
        requests_30d=int(u.requests),
        cost_usd_30d=float(u.cost),
        period_start=period_start,
        period_end=period_end,
    )


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
        filters.append(ErrorLog.tenant_id == tid)
    if unresolved_only:
        filters.append(ErrorLog.is_resolved.is_(False))

    total_result = await db.execute(select(func.count(ErrorLog.id)).where(*filters))
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(ErrorLog)
        .where(*filters)
        .order_by(ErrorLog.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    errors = rows_result.scalars().all()

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

    # All errors in period
    base_filters = [ErrorLog.created_at >= cutoff]

    total_result = await db.execute(
        select(func.count(ErrorLog.id)).where(*base_filters)
    )
    total_errors = total_result.scalar_one()

    unresolved_result = await db.execute(
        select(func.count(ErrorLog.id)).where(
            ErrorLog.created_at >= cutoff, ErrorLog.is_resolved.is_(False)
        )
    )
    unresolved = unresolved_result.scalar_one()

    # By severity
    sev_result = await db.execute(
        select(ErrorLog.severity, func.count(ErrorLog.id))
        .where(*base_filters)
        .group_by(ErrorLog.severity)
    )
    by_severity = {row.severity: row.count for row in sev_result.all()}

    # By type
    type_result = await db.execute(
        select(ErrorLog.error_type, func.count(ErrorLog.id))
        .where(*base_filters)
        .group_by(ErrorLog.error_type)
    )
    by_type = {row.error_type: row.count for row in type_result.all()}

    # By tenant (top 20)
    bt_result = await db.execute(
        select(
            ErrorLog.tenant_id,
            func.count(ErrorLog.id).label("cnt"),
        )
        .where(*base_filters)
        .group_by(ErrorLog.tenant_id)
        .order_by(func.count(ErrorLog.id).desc())
        .limit(20)
    )
    bt_rows = bt_result.all()
    tids = {r.tenant_id for r in bt_rows}
    tn_map: dict[uuid.UUID, str] = {}
    if tids:
        tn_r = await db.execute(
            select(Tenant.id, Tenant.name).where(Tenant.id.in_(tids))
        )
        tn_map = {str(r.id): r.name for r in tn_r.fetchall()}
    by_tenant = [
        {
            "tenant_id": str(r.tenant_id),
            "tenant_name": tn_map.get(str(r.tenant_id), "—"),
            "count": r.cnt,
        }
        for r in bt_rows
    ]

    # Daily trend
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

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    filters = [
        ErrorLog.tenant_id == tid,
        ErrorLog.created_at >= cutoff,
    ]
    if severity:
        filters.append(ErrorLog.severity == severity)
    if error_type:
        filters.append(ErrorLog.error_type == error_type)
    if unresolved_only:
        filters.append(ErrorLog.is_resolved.is_(False))

    total_result = await db.execute(select(func.count(ErrorLog.id)).where(*filters))
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(ErrorLog)
        .where(*filters)
        .order_by(ErrorLog.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    errors = rows_result.scalars().all()

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

    return TenantErrorSummary(
        total_errors=total_errors,
        unresolved=unresolved,
        by_severity=by_severity,
        by_type=by_type,
        trend=trend,
        days=days,
    )


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
    if tenant_id:
        try:
            tid = uuid.UUID(tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")
        filters.append(ApiAccessLog.tenant_id == tid)
    if endpoint:
        filters.append(ApiAccessLog.endpoint.ilike(f"%{endpoint}%"))
    if status_code is not None:
        filters.append(ApiAccessLog.status_code == status_code)

    total_result = await db.execute(select(func.count(ApiAccessLog.id)).where(*filters))
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(ApiAccessLog)
        .where(*filters)
        .order_by(ApiAccessLog.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    entries = rows_result.scalars().all()

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
    filters = [ApiAccessLog.created_at >= cutoff]
    if tenant_id:
        try:
            tid = uuid.UUID(tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")
        filters.append(ApiAccessLog.tenant_id == tid)

    total_r = await db.execute(select(func.count(ApiAccessLog.id)).where(*filters))
    total_requests = total_r.scalar_one()

    # By status code
    status_r = await db.execute(
        select(
            ApiAccessLog.status_code,
            func.count(ApiAccessLog.id).label("cnt"),
        )
        .where(*filters)
        .group_by(ApiAccessLog.status_code)
    )
    by_status = {str(row.status_code): row.cnt for row in status_r.all()}

    # Avg latency
    lat_r = await db.execute(select(func.avg(ApiAccessLog.latency_ms)).where(*filters))
    avg_latency_ms = lat_r.scalar_one()

    # By endpoint (top 20)
    ep_r = await db.execute(
        select(
            ApiAccessLog.endpoint,
            func.count(ApiAccessLog.id).label("cnt"),
        )
        .where(*filters)
        .group_by(ApiAccessLog.endpoint)
        .order_by(func.count(ApiAccessLog.id).desc())
        .limit(20)
    )
    by_endpoint = [{"endpoint": row.endpoint, "count": row.cnt} for row in ep_r.all()]

    # By tenant (top 20)
    bt_r = await db.execute(
        select(
            ApiAccessLog.tenant_id,
            func.count(ApiAccessLog.id).label("cnt"),
        )
        .where(*filters)
        .group_by(ApiAccessLog.tenant_id)
        .order_by(func.count(ApiAccessLog.id).desc())
        .limit(20)
    )
    bt_rows = bt_r.all()
    tids = {r.tenant_id for r in bt_rows}
    tn_map: dict[uuid.UUID, str] = {}
    if tids:
        tn_r = await db.execute(
            select(Tenant.id, Tenant.name).where(Tenant.id.in_(tids))
        )
        tn_map = {str(r.id): r.name for r in tn_r.fetchall()}
    by_tenant = [
        {
            "tenant_id": str(r.tenant_id),
            "tenant_name": tn_map.get(str(r.tenant_id), "—"),
            "count": r.cnt,
        }
        for r in bt_rows
    ]

    return AccessLogSummary(
        total_requests=total_requests,
        by_status=by_status,
        avg_latency_ms=round(float(avg_latency_ms), 2) if avg_latency_ms else None,
        by_endpoint=by_endpoint,
        by_tenant=by_tenant,
        days=hours // 24 or 1,
    )
