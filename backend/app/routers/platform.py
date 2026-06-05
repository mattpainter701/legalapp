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
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.conversation import UsageRecord
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User

settings = get_settings()
router = APIRouter(prefix="/platform", tags=["platform"])


# ── Auth ───────────────────────────────────────────────────────────────────────


def _require_platform_key(request: Request) -> None:
    key = request.headers.get("X-Platform-Key", "")
    if not settings.PLATFORM_SECRET_KEY or not hmac.compare_digest(
        key, settings.PLATFORM_SECRET_KEY
    ):
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
    llm_provider: Optional[str] = (
        None  # "deepseek"|"opencode"|"openrouter"|"anthropic"|"azure"|"gemini"
    )
    llm_model: Optional[str] = None  # optional model override


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
        },
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

    # LLM provider / model — stored on TenantSettings
    if body.llm_provider is not None or body.llm_model is not None:
        VALID_PROVIDERS = {
            "deepseek",
            "opencode",
            "openrouter",
            "anthropic",
            "azure",
            "gemini",
        }
        if body.llm_provider is not None and body.llm_provider not in VALID_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"llm_provider must be one of: {', '.join(sorted(VALID_PROVIDERS))}",
            )

        ts_result = await db.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
        )
        ts = ts_result.scalar_one_or_none()
        if not ts:
            ts = TenantSettings(tenant_id=tenant.id)
            db.add(ts)
        if body.llm_provider is not None:
            ts.default_llm_provider = body.llm_provider
        if body.llm_model is not None:
            ts.default_llm_model = body.llm_model

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


@router.get("/llm-providers")
async def list_llm_providers(request: Request):
    """List available LLM providers and their status (checks env vars)."""
    _require_platform_key(request)

    def _provider(key: str, label: str, free_tier: bool, models: list[str]) -> dict:
        configured = bool(
            (key == "deepseek" and (settings.DEEPSEEK_API_KEY or settings.OPENCODE_KEY))
            or (
                key == "opencode"
                and (settings.OPENCODE_KEY or settings.DEEPSEEK_API_KEY)
            )
            or (key == "openrouter" and settings.OPENROUTER_API_KEY)
            or (key == "anthropic" and settings.ANTHROPIC_API_KEY)
            or (
                key == "azure"
                and settings.AZURE_OPENAI_ENDPOINT
                and settings.AZURE_OPENAI_KEY
            )
            or (key == "gemini" and settings.GEMINI_API_KEY)
        )
        return {
            "key": key,
            "label": label,
            "configured": configured,
            "free_tier": free_tier,
            "models": models,
        }

    openrouter_models = [
        m.strip() for m in settings.OPENROUTER_FREE_MODELS.split(",") if m.strip()
    ]

    providers = [
        _provider("deepseek", "DeepSeek", False, [settings.PRIMARY_LLM]),
        _provider("opencode", "OpenCode Zen", True, [settings.PRIMARY_LLM]),
        _provider(
            "openrouter",
            "OpenRouter",
            True,
            openrouter_models if openrouter_models else ["google/gemma-4-31b-it:free"],
        ),
        _provider("anthropic", "Anthropic Claude", False, [settings.PREMIUM_LLM]),
        _provider(
            "azure",
            "Azure OpenAI",
            False,
            [settings.AZURE_OPENAI_DEPLOYMENT]
            if settings.AZURE_OPENAI_DEPLOYMENT
            else [],
        ),
        _provider("gemini", "Google Gemini", True, ["gemini-2.0-flash"]),
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
                pg_size_pretty(pg_total_relation_size(oid)) AS total_size
            FROM pg_stat_user_tables
            ORDER BY n_live_tup DESC
        """)
    )
    tables = [
        {"table": r.table_name, "rows": r.row_count, "size": r.total_size}
        for r in rows.fetchall()
    ]

    # Real provider health from env vars
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
            "name": "DeepSeek / OpenCode",
            "online": bool(settings.DEEPSEEK_API_KEY or settings.OPENCODE_KEY),
        },
        {
            "name": "OpenCode Zen",
            "online": bool(settings.OPENCODE_KEY or settings.DEEPSEEK_API_KEY),
        },
        {
            "name": "OpenRouter",
            "online": bool(settings.OPENROUTER_API_KEY),
        },
        {
            "name": "Anthropic Claude",
            "online": bool(settings.ANTHROPIC_API_KEY),
        },
        {
            "name": "Azure OpenAI",
            "online": bool(
                settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_KEY
            ),
        },
        {
            "name": "Google Gemini",
            "online": bool(settings.GEMINI_API_KEY),
        },
    ]

    return {
        "tables": tables,
        "services": services,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
