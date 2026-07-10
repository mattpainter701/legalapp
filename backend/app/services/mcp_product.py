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
from app.models.tenant import Tenant
from app.models.durable_job import DurableJob
from app.services.gateway_privacy import retained_gateway_query_text
from app.services.durable_jobs import enqueue_job

settings = get_settings()
_fallback_burst_hits: dict[str, tuple[int, float]] = {}

DEFAULT_ALLOWED_TOOLS = [
    "search_caselaw",
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


def generate_product_key() -> str:
    return "clmcp_" + secrets.token_urlsafe(32)


def hash_key(raw_key: str) -> str:
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


def ensure_tool_allowed(product_key: MCPProductKey | Any, tool_name: str) -> None:
    allowed = getattr(product_key, "allowed_tools", None) or DEFAULT_ALLOWED_TOOLS
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
) -> tuple[MCPProductKey, str]:
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    ensure_mcp_product_access(tenant)
    monthly_limit = monthly_call_limit or settings.MCP_DEFAULT_MONTHLY_CALL_LIMIT
    burst_limit = burst_limit_per_minute or settings.MCP_DEFAULT_BURST_LIMIT_PER_MINUTE
    if not 1 <= monthly_limit <= settings.MCP_MAX_MONTHLY_CALL_LIMIT:
        raise HTTPException(status_code=400, detail="Invalid monthly MCP call limit")
    if not 1 <= burst_limit <= settings.MCP_MAX_BURST_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=400, detail="Invalid MCP burst limit")
    raw_key = generate_product_key()
    product_key = MCPProductKey(
        tenant_id=tenant_id,
        name=name.strip() or "MCP API key",
        key_hash=hash_key(raw_key),
        key_prefix=raw_key[:12],
        allowed_tools=normalize_allowed_tools(allowed_tools),
        monthly_call_limit=monthly_limit,
        burst_limit_per_minute=burst_limit,
        created_by_user_id=user_id,
    )
    db.add(product_key)
    await db.commit()
    await db.refresh(product_key)
    return product_key, raw_key


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
        result = await db.execute(
            select(MCPProductKey, Tenant)
            .join(Tenant, Tenant.id == MCPProductKey.tenant_id)
            .where(MCPProductKey.key_hash == hash_key(raw_key))
        )
    finally:
        await db.execute(text("SELECT set_config('app.rls_bypass', 'off', true)"))
    row = result.first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid MCP API key")
    product_key, tenant = row
    if not product_key.is_active or product_key.revoked_at is not None:
        raise HTTPException(status_code=401, detail="MCP API key has been revoked")
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


async def record_mcp_usage(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    tool_name: str,
    auth_type: str,
    status_code: int,
    product_key_id: uuid.UUID | None = None,
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
    stripe_value = 1 if product_key_id and status_code < 400 else 0
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
