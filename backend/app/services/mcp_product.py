import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import stripe
from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.mcp_product import MCPProductKey, MCPUsageEvent
from app.models.durable_job import DurableJob
from app.models.tenant import Tenant
from app.models.user import User
from app.services.gateway_privacy import retained_gateway_query_text
from app.services.durable_jobs import enqueue_job

settings = get_settings()
_fallback_burst_hits: dict[str, tuple[int, float]] = {}

RESEARCH_ALLOWED_TOOLS = [
    "search_caselaw",
    "search_legal_authorities",
    "get_case_details",
    "get_full_opinion",
    "find_similar_cases",
    "search_by_citation",
    "validate_citation",
    "normalize_citation",
    "get_citation_network",
    "get_authority_treatment",
    "search_by_jurisdiction",
    "search_recent_authority",
    "get_court_info",
    "get_court_coverage",
    "search_dockets",
    "export_research_bundle",
    "sync_status",
    "corpus_status",
]
# Public Research MCP is intentionally retrieval/status/export only. Workspace
# and document tools belong to the OAuth-backed Workspace MCP product and must
# not become reachable through a stale or manually edited research key.
DEFAULT_ALLOWED_TOOLS = RESEARCH_ALLOWED_TOOLS


def generate_product_key() -> str:
    return "lhrk_" + secrets.token_urlsafe(32)


def hash_key(raw_key: str) -> str:
    # Product keys are 256-bit CSPRNG bearer tokens, not user-chosen passwords;
    # this deterministic digest is an indexed lookup value, never a password hash.
    # codeql[py/weak-sensitive-data-hashing]
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def mask_key(raw_key_or_prefix: str, suffix: str | None = None) -> str:
    if suffix is not None:
        return f"{raw_key_or_prefix}...{suffix}"
    return f"{raw_key_or_prefix[:12]}...{raw_key_or_prefix[-4:]}"


def normalize_allowed_tools(tools: list[str] | None) -> list[str]:
    if not tools:
        return list(DEFAULT_ALLOWED_TOOLS)
    normalized = []
    for tool in tools:
        value = str(tool).strip()
        if not value:
            continue
        if value not in DEFAULT_ALLOWED_TOOLS:
            raise HTTPException(status_code=400, detail=f"Unknown MCP tool: {value}")
        if value not in normalized:
            normalized.append(value)
    return normalized


def effective_allowed_tools(product_key: MCPProductKey | Any) -> list[str]:
    """Return only tools that belong to the public Research MCP product."""
    stored = getattr(product_key, "allowed_tools", None)
    if not stored:
        return list(DEFAULT_ALLOWED_TOOLS)
    return [tool for tool in stored if tool in DEFAULT_ALLOWED_TOOLS]


def ensure_tool_allowed(product_key: MCPProductKey | Any, tool_name: str) -> None:
    if tool_name not in DEFAULT_ALLOWED_TOOLS:
        raise HTTPException(
            status_code=403,
            detail="Tool is not available through the LawHand Research MCP",
        )
    allowed = effective_allowed_tools(product_key)
    if tool_name not in allowed:
        raise HTTPException(
            status_code=403, detail="MCP key is not allowed to call this tool"
        )


def current_month_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def product_key_status(
    product_key: MCPProductKey | Any, *, now: datetime | None = None
) -> str:
    if (
        not getattr(product_key, "is_active", False)
        or getattr(product_key, "revoked_at", None) is not None
    ):
        return "revoked"
    expires_at = getattr(product_key, "expires_at", None)
    if expires_at is not None and _aware_utc(expires_at) <= (
        now or datetime.now(timezone.utc)
    ):
        return "expired"
    return "active"


async def _validate_assignee(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assigned_to_user_id: uuid.UUID | None,
) -> None:
    if assigned_to_user_id is None:
        return
    assignee = await db.scalar(
        select(User).where(
            User.id == assigned_to_user_id,
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )
    if assignee is None:
        raise HTTPException(
            status_code=400, detail="Assigned staff member is unavailable"
        )


def _validate_key_controls(
    *,
    monthly_call_limit: int,
    burst_limit_per_minute: int,
    monthly_budget_cents: int | None,
    unit_price_cents: int,
    expires_at: datetime | None,
) -> None:
    if (
        not isinstance(monthly_call_limit, int)
        or isinstance(monthly_call_limit, bool)
        or not 1 <= monthly_call_limit <= settings.MCP_MAX_MONTHLY_CALL_LIMIT
    ):
        raise HTTPException(status_code=400, detail="Invalid monthly MCP call limit")
    if (
        not isinstance(burst_limit_per_minute, int)
        or isinstance(burst_limit_per_minute, bool)
        or not 1 <= burst_limit_per_minute <= settings.MCP_MAX_BURST_LIMIT_PER_MINUTE
    ):
        raise HTTPException(status_code=400, detail="Invalid MCP burst limit")
    if unit_price_cents < 1:
        raise HTTPException(status_code=503, detail="MCP product price is invalid")
    if monthly_budget_cents is not None:
        if (
            not isinstance(monthly_budget_cents, int)
            or isinstance(monthly_budget_cents, bool)
            or monthly_budget_cents > 2_000_000_000
        ):
            raise HTTPException(status_code=400, detail="Invalid monthly MCP budget")
        if monthly_budget_cents < unit_price_cents:
            raise HTTPException(
                status_code=400,
                detail="Monthly budget must cover at least one successful MCP call",
            )
    if expires_at is not None and _aware_utc(expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400, detail="Key expiration must be in the future"
        )


async def deliver_mcp_meter_event(payload: dict[str, Any]) -> dict[str, str]:
    """Deliver one durable usage event to Stripe with a stable identifier."""
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_MCP_METER_EVENT_NAME:
        raise RuntimeError("Stripe MCP metering is not configured")
    meter_event = getattr(getattr(stripe, "billing", None), "MeterEvent", None)
    if meter_event is None:
        raise RuntimeError("Stripe SDK does not expose billing.MeterEvent")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    await asyncio.to_thread(
        meter_event.create,
        event_name=settings.STRIPE_MCP_METER_EVENT_NAME,
        payload={
            "stripe_customer_id": payload["stripe_customer_id"],
            "value": str(payload.get("value", 1)),
        },
        identifier=payload["identifier"],
    )
    return {"identifier": payload["identifier"]}


def ensure_mcp_product_access(tenant: Tenant | Any) -> None:
    if not settings.MCP_PRODUCT_ENABLED:
        raise HTTPException(status_code=503, detail="MCP product access is disabled")
    if not getattr(tenant, "is_active", False):
        raise HTTPException(status_code=403, detail="Tenant is inactive")
    if getattr(tenant, "mcp_entitlement_status", "disabled") != "enabled":
        raise HTTPException(status_code=403, detail="MCP entitlement is not active")
    if getattr(tenant, "mcp_billing_status", "disabled") != "active":
        raise HTTPException(status_code=402, detail="MCP billing is not active")
    if not getattr(tenant, "stripe_customer_id", None):
        raise HTTPException(
            status_code=402, detail="MCP billing customer is not configured"
        )
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_MCP_METER_EVENT_NAME:
        raise HTTPException(
            status_code=503, detail="MCP usage metering is not configured"
        )


async def create_product_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    monthly_call_limit: int | None = None,
    burst_limit_per_minute: int | None = None,
    allowed_tools: list[str] | None = None,
    purpose: str | None = None,
    assigned_to_user_id: uuid.UUID | None = None,
    monthly_budget_cents: int | None = None,
    expires_at: datetime | None = None,
) -> tuple[MCPProductKey, str]:
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    ensure_mcp_product_access(tenant)
    monthly_limit = monthly_call_limit or settings.MCP_DEFAULT_MONTHLY_CALL_LIMIT
    burst_limit = burst_limit_per_minute or settings.MCP_DEFAULT_BURST_LIMIT_PER_MINUTE
    unit_price_cents = settings.MCP_PRODUCT_CALL_PRICE_CENTS
    _validate_key_controls(
        monthly_call_limit=monthly_limit,
        burst_limit_per_minute=burst_limit,
        monthly_budget_cents=monthly_budget_cents,
        unit_price_cents=unit_price_cents,
        expires_at=expires_at,
    )
    await _validate_assignee(
        db,
        tenant_id=tenant_id,
        assigned_to_user_id=assigned_to_user_id,
    )
    clean_name = name.strip() or "MCP API key"
    clean_purpose = (purpose or "").strip() or None
    if len(clean_name) > 120:
        raise HTTPException(status_code=400, detail="Key name is too long")
    if clean_purpose is not None and len(clean_purpose) > 255:
        raise HTTPException(status_code=400, detail="Key purpose is too long")
    raw_key = generate_product_key()
    product_key = MCPProductKey(
        tenant_id=tenant_id,
        name=clean_name,
        purpose=clean_purpose,
        assigned_to_user_id=assigned_to_user_id,
        key_hash=hash_key(raw_key),
        key_prefix=raw_key[:12],
        allowed_tools=normalize_allowed_tools(allowed_tools),
        monthly_call_limit=monthly_limit,
        monthly_budget_cents=monthly_budget_cents,
        unit_price_cents=unit_price_cents,
        burst_limit_per_minute=burst_limit,
        expires_at=expires_at,
        created_by_user_id=user_id,
    )
    db.add(product_key)
    await db.commit()
    await db.refresh(product_key)
    return product_key, raw_key


async def update_product_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    changes: dict[str, Any],
) -> MCPProductKey | None:
    product_key = await db.scalar(
        select(MCPProductKey).where(
            MCPProductKey.id == key_id,
            MCPProductKey.tenant_id == tenant_id,
        )
    )
    if product_key is None:
        return None
    if product_key_status(product_key) == "revoked":
        raise HTTPException(status_code=409, detail="Revoked MCP keys cannot be edited")

    assigned_to_user_id = changes.get(
        "assigned_to_user_id", product_key.assigned_to_user_id
    )
    await _validate_assignee(
        db,
        tenant_id=tenant_id,
        assigned_to_user_id=assigned_to_user_id,
    )
    monthly_limit = changes.get("monthly_call_limit", product_key.monthly_call_limit)
    burst_limit = changes.get(
        "burst_limit_per_minute", product_key.burst_limit_per_minute
    )
    budget_cents = changes.get("monthly_budget_cents", product_key.monthly_budget_cents)
    expires_at = changes.get("expires_at", product_key.expires_at)
    _validate_key_controls(
        monthly_call_limit=monthly_limit,
        burst_limit_per_minute=burst_limit,
        monthly_budget_cents=budget_cents,
        unit_price_cents=product_key.unit_price_cents,
        expires_at=expires_at,
    )

    for field, value in changes.items():
        if field == "name":
            value = str(value or "").strip()
            if not value:
                raise HTTPException(status_code=400, detail="Key name is required")
            if len(value) > 120:
                raise HTTPException(status_code=400, detail="Key name is too long")
        elif field == "purpose":
            value = str(value or "").strip() or None
            if value is not None and len(value) > 255:
                raise HTTPException(status_code=400, detail="Key purpose is too long")
        elif field == "allowed_tools":
            value = normalize_allowed_tools(value)
        setattr(product_key, field, value)
    await db.commit()
    await db.refresh(product_key)
    return product_key


async def list_product_keys(
    db: AsyncSession, tenant_id: uuid.UUID
) -> list[MCPProductKey]:
    result = await db.execute(
        select(MCPProductKey)
        .where(MCPProductKey.tenant_id == tenant_id)
        .order_by(MCPProductKey.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_product_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        update(MCPProductKey)
        .where(MCPProductKey.id == key_id, MCPProductKey.tenant_id == tenant_id)
        .values(is_active=False, revoked_at=datetime.now(timezone.utc))
        .returning(MCPProductKey.id)
    )
    revoked = result.scalar_one_or_none() is not None
    await db.commit()
    return revoked


async def resolve_product_key(
    db: AsyncSession,
    raw_key: str,
) -> tuple[MCPProductKey, Tenant]:
    await db.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
    try:
        row = (
            await db.execute(
                select(MCPProductKey, Tenant)
                .join(Tenant, Tenant.id == MCPProductKey.tenant_id)
                .where(MCPProductKey.key_hash == hash_key(raw_key))
            )
        ).first()
        assigned_user = None
        if row and row[0].assigned_to_user_id is not None:
            assigned_user = await db.scalar(
                select(User).where(User.id == row[0].assigned_to_user_id)
            )
    finally:
        await db.execute(text("SELECT set_config('app.rls_bypass', 'off', true)"))
    if not row:
        raise HTTPException(status_code=401, detail="Invalid MCP API key")
    product_key, tenant = row
    status = product_key_status(product_key)
    if status == "revoked":
        raise HTTPException(status_code=401, detail="MCP API key has been revoked")
    if status == "expired":
        raise HTTPException(status_code=401, detail="MCP API key has expired")
    if product_key.assigned_to_user_id is not None and (
        assigned_user is None or not assigned_user.is_active
    ):
        raise HTTPException(status_code=401, detail="MCP API key assignee is inactive")
    ensure_mcp_product_access(tenant)
    return product_key, tenant


def _fallback_burst_increment(key: str) -> tuple[int, int]:
    now = datetime.now(timezone.utc).timestamp()
    expires_at = (int(now) // 60 + 1) * 60
    count, existing_expiry = _fallback_burst_hits.get(key, (0, expires_at))
    if existing_expiry <= now:
        count, existing_expiry = 0, expires_at
    count += 1
    _fallback_burst_hits[key] = (count, existing_expiry)
    return count, max(1, int(existing_expiry - now))


async def enforce_product_key_burst_limit(
    redis, product_key: MCPProductKey | Any
) -> None:
    limit = int(
        getattr(product_key, "burst_limit_per_minute", None)
        or settings.MCP_DEFAULT_BURST_LIMIT_PER_MINUTE
    )
    bucket = int(datetime.now(timezone.utc).timestamp()) // 60
    key = f"rate:mcp:key:{product_key.id}:{bucket}"
    try:
        if redis is None:
            if not settings.DEV_MODE:
                raise HTTPException(
                    status_code=503, detail="MCP rate limiter is unavailable"
                )
            count, retry_after = _fallback_burst_increment(key)
        else:
            count, ttl = await redis.eval(
                "local c=redis.call('INCR',KEYS[1]); "
                "if c==1 then redis.call('EXPIRE',KEYS[1],60) end; "
                "return {c,redis.call('TTL',KEYS[1])}",
                1,
                key,
            )
            count, retry_after = int(count), max(1, int(ttl))
    except RedisError as exc:
        if not settings.DEV_MODE:
            raise HTTPException(
                status_code=503, detail="MCP rate limiter is unavailable"
            ) from exc
        count, retry_after = _fallback_burst_increment(key)
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail="MCP API key burst limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


async def enforce_product_key_quota(
    db: AsyncSession,
    product_key: MCPProductKey | Any,
) -> None:
    limit = int(
        getattr(product_key, "monthly_call_limit", None)
        or settings.MCP_DEFAULT_MONTHLY_CALL_LIMIT
    )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"mcp_product_key:{product_key.id}"},
    )
    start, end = current_month_window()
    result = await db.execute(
        select(func.count(MCPUsageEvent.id)).where(
            MCPUsageEvent.product_key_id == product_key.id,
            MCPUsageEvent.created_at >= start,
            MCPUsageEvent.created_at < end,
            MCPUsageEvent.status_code < 400,
        )
    )
    used = int(result.scalar_one())
    if used >= int(limit):
        raise HTTPException(
            status_code=429, detail="MCP API key monthly quota exceeded"
        )
    budget_cents = getattr(product_key, "monthly_budget_cents", None)
    if budget_cents is not None:
        unit_price_cents = int(
            getattr(product_key, "unit_price_cents", None)
            or settings.MCP_PRODUCT_CALL_PRICE_CENTS
        )
        if (used + 1) * unit_price_cents > int(budget_cents):
            raise HTTPException(
                status_code=429, detail="MCP API key monthly budget exceeded"
            )


async def enforce_research_oauth_quota(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Apply the default allowance across a user's rotating OAuth grants."""

    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"mcp_oauth_user:{tenant_id}:{user_id}"},
    )
    start, end = current_month_window()
    used = int(
        (
            await db.execute(
                select(func.count(MCPUsageEvent.id)).where(
                    MCPUsageEvent.tenant_id == tenant_id,
                    MCPUsageEvent.user_id == user_id,
                    MCPUsageEvent.auth_type == "research_oauth",
                    MCPUsageEvent.created_at >= start,
                    MCPUsageEvent.created_at < end,
                    MCPUsageEvent.status_code < 400,
                )
            )
        ).scalar_one()
    )
    if used >= settings.MCP_DEFAULT_MONTHLY_CALL_LIMIT:
        raise HTTPException(
            status_code=429, detail="Research MCP monthly quota exceeded"
        )


async def record_mcp_usage(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    tool_name: str,
    auth_type: str,
    status_code: int,
    product_key_id: uuid.UUID | None = None,
    oauth_grant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    transport: str = "rest",
    result_count: int = 0,
    latency_ms: int | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    error_class: str | None = None,
    query_text: str | None = None,
    metadata_json: dict | None = None,
) -> MCPUsageEvent:
    event = MCPUsageEvent(
        tenant_id=tenant_id,
        product_key_id=product_key_id,
        oauth_grant_id=oauth_grant_id,
        user_id=user_id,
        auth_type=auth_type,
        transport=transport,
        tool_name=tool_name,
        status_code=status_code,
        result_count=result_count,
        latency_ms=latency_ms,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        error_class=error_class,
        query_text=retained_gateway_query_text(query_text),
        metadata_json=metadata_json,
    )
    db.add(event)
    if product_key_id:
        await db.execute(
            update(MCPProductKey)
            .where(MCPProductKey.id == product_key_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
    await db.flush()
    stripe_value = (
        1
        if (product_key_id is not None or oauth_grant_id is not None)
        and status_code < 400
        else 0
    )
    if stripe_value:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if not tenant or not tenant.stripe_customer_id:
            raise RuntimeError("MCP usage cannot be metered without a Stripe customer")
        meter_job = await enqueue_job(
            db,
            tenant_id=tenant_id,
            kind="mcp_stripe_meter",
            idempotency_key=str(event.id),
            payload={
                "usage_event_id": str(event.id),
                "stripe_customer_id": tenant.stripe_customer_id,
                "value": stripe_value,
                "identifier": f"mcp_usage_{event.id}",
            },
        )
        meter_job.max_attempts = max(meter_job.max_attempts, 12)
    await db.commit()
    return event


async def record_internal_chat_mcp_usage(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    tool_name: str,
    status_code: int,
    result_count: int,
    latency_ms: int | None = None,
) -> None:
    await record_mcp_usage(
        db=db,
        tenant_id=tenant_id,
        user_id=user_id,
        product_key_id=None,
        auth_type="internal_chat",
        transport="internal",
        tool_name=tool_name,
        status_code=status_code,
        result_count=result_count,
        latency_ms=latency_ms,
    )


async def usage_summary(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    days: int = 30,
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 366)))
    total_result = await db.execute(
        select(
            func.count(MCPUsageEvent.id),
            func.coalesce(func.sum(MCPUsageEvent.result_count), 0),
        ).where(MCPUsageEvent.tenant_id == tenant_id, MCPUsageEvent.created_at >= since)
    )
    total_calls, total_results = total_result.one()
    by_key_result = await db.execute(
        select(
            MCPUsageEvent.product_key_id,
            func.count(MCPUsageEvent.id),
            func.coalesce(func.sum(MCPUsageEvent.result_count), 0),
        )
        .where(MCPUsageEvent.tenant_id == tenant_id, MCPUsageEvent.created_at >= since)
        .group_by(MCPUsageEvent.product_key_id)
    )
    return {
        "days": days,
        "total_calls": int(total_calls or 0),
        "total_results": int(total_results or 0),
        "by_key": [
            {
                "product_key_id": str(key_id) if key_id else None,
                "calls": int(calls or 0),
                "results": int(results or 0),
            }
            for key_id, calls, results in by_key_result.all()
        ],
    }


async def monthly_key_usage(
    db: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, dict[str, int | float]]:
    start, end = current_month_window()
    rows = (
        await db.execute(
            select(
                MCPUsageEvent.product_key_id,
                MCPUsageEvent.status_code,
                func.count(MCPUsageEvent.id),
                func.coalesce(func.sum(MCPUsageEvent.result_count), 0),
            )
            .where(
                MCPUsageEvent.tenant_id == tenant_id,
                MCPUsageEvent.product_key_id.is_not(None),
                MCPUsageEvent.created_at >= start,
                MCPUsageEvent.created_at < end,
            )
            .group_by(MCPUsageEvent.product_key_id, MCPUsageEvent.status_code)
        )
    ).all()
    key_prices = {
        key_id: int(price or settings.MCP_PRODUCT_CALL_PRICE_CENTS)
        for key_id, price in (
            await db.execute(
                select(MCPProductKey.id, MCPProductKey.unit_price_cents).where(
                    MCPProductKey.tenant_id == tenant_id
                )
            )
        ).all()
    }
    summary: dict[str, dict[str, int | float]] = {}
    for key_id, status_code, count, results in rows:
        item = summary.setdefault(
            str(key_id),
            {
                "calls": 0,
                "successful_calls": 0,
                "failed_calls": 0,
                "results": 0,
                "charge_cents": 0,
                "charge_usd": 0.0,
            },
        )
        call_count = int(count or 0)
        item["calls"] += call_count
        item["results"] += int(results or 0)
        if int(status_code) < 400:
            item["successful_calls"] += call_count
        else:
            item["failed_calls"] += call_count
    for key_id, item in summary.items():
        charge_cents = int(item["successful_calls"]) * key_prices.get(
            uuid.UUID(key_id), settings.MCP_PRODUCT_CALL_PRICE_CENTS
        )
        item["charge_cents"] = charge_cents
        item["charge_usd"] = charge_cents / 100
    return summary


async def metering_outbox_summary(
    db: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, int]:
    rows = (
        await db.execute(
            select(DurableJob.status, func.count(DurableJob.id))
            .where(
                DurableJob.tenant_id == tenant_id,
                DurableJob.kind == "mcp_stripe_meter",
            )
            .group_by(DurableJob.status)
        )
    ).all()
    counts = {status: int(count) for status, count in rows}
    return {
        "pending": counts.get("pending", 0),
        "running": counts.get("running", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
    }
