"""Shared abuse controls for public MCP Streamable HTTP transports.

Nginx provides the first IP-level boundary. These controls live at the ASGI
boundary as a second, identity-aware layer so direct/internal traffic and
distributed clients cannot bypass request-size or principal limits.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from fastapi import HTTPException
from redis.exceptions import RedisError
from starlette.requests import Request
from starlette.types import Message, Receive, Scope

from app.config import get_settings


settings = get_settings()

_DUAL_RATE_SCRIPT = """
local primary_limit = tonumber(ARGV[1])
local window_ttl = tonumber(ARGV[2])
local primary_count = redis.call('INCR', KEYS[1])
if primary_count == 1 then redis.call('EXPIRE', KEYS[1], window_ttl) end
local tenant_count = tonumber(redis.call('GET', KEYS[2]) or '0')
local tenant_ttl = redis.call('TTL', KEYS[2])
if primary_count <= primary_limit then
  tenant_count = redis.call('INCR', KEYS[2])
  if tenant_count == 1 then redis.call('EXPIRE', KEYS[2], window_ttl) end
  tenant_ttl = redis.call('TTL', KEYS[2])
end
return {
  primary_count, redis.call('TTL', KEYS[1]),
  tenant_count, tenant_ttl
}
"""

_fallback_rate_hits: dict[str, tuple[int, float]] = {}


class MCPRequestBodyTooLarge(Exception):
    """Raised before protocol parsing when an MCP request exceeds its cap."""


def _fallback_increment(key: str) -> tuple[int, int]:
    """Increment a development-only, minute-aligned in-process counter."""

    now = time.time()
    if len(_fallback_rate_hits) > 2048:
        expired = [
            existing_key
            for existing_key, (_, expiry) in _fallback_rate_hits.items()
            if expiry <= now
        ]
        for existing_key in expired:
            _fallback_rate_hits.pop(existing_key, None)

    expires_at = (int(now) // 60 + 1) * 60
    count, existing_expiry = _fallback_rate_hits.get(key, (0, expires_at))
    if existing_expiry <= now:
        count, existing_expiry = 0, expires_at
    count += 1
    _fallback_rate_hits[key] = (count, existing_expiry)
    return count, max(1, int(existing_expiry - now))


async def _enforce_dual_principal_limit(
    request: Request,
    *,
    primary_key: str,
    tenant_key: str,
    primary_limit: int,
    window_ttl: int,
    tenant_limit: int,
    unavailable_detail: str,
    primary_limit_detail: str,
    tenant_limit_detail: str,
) -> None:
    redis = getattr(request.app.state, "redis", None)

    try:
        if redis is None:
            if not settings.DEV_MODE:
                raise HTTPException(status_code=503, detail=unavailable_detail)
            primary_count, primary_ttl = _fallback_increment(primary_key)
            tenant_count, tenant_ttl = _fallback_increment(tenant_key)
        else:
            result = await redis.eval(
                _DUAL_RATE_SCRIPT,
                2,
                primary_key,
                tenant_key,
                primary_limit,
                window_ttl,
            )
            primary_count, primary_ttl, tenant_count, tenant_ttl = map(int, result)
    except HTTPException:
        raise
    except (RedisError, TypeError, ValueError) as exc:
        if not settings.DEV_MODE:
            raise HTTPException(status_code=503, detail=unavailable_detail) from exc
        primary_count, primary_ttl = _fallback_increment(primary_key)
        tenant_count, tenant_ttl = _fallback_increment(tenant_key)

    if primary_count > primary_limit:
        raise HTTPException(
            status_code=429,
            detail=primary_limit_detail,
            headers={"Retry-After": str(max(1, primary_ttl))},
        )
    if tenant_count > tenant_limit:
        raise HTTPException(
            status_code=429,
            detail=tenant_limit_detail,
            headers={"Retry-After": str(max(1, tenant_ttl))},
        )


def _minute_window() -> tuple[int, int]:
    now = int(time.time())
    return now // 60, max(1, 60 - (now % 60))


async def enforce_workspace_request_limit(request: Request, identity: Any) -> None:
    """Limit all workspace protocol traffic by OAuth token and tenant."""

    bucket, window_ttl = _minute_window()
    await _enforce_dual_principal_limit(
        request,
        primary_key=(f"rate:mcp:workspace:token:{identity.token_id}:{bucket}"),
        tenant_key=(f"rate:mcp:workspace:tenant:{identity.tenant_id}:{bucket}"),
        primary_limit=settings.WORKSPACE_MCP_TOKEN_REQUESTS_PER_MINUTE,
        tenant_limit=settings.WORKSPACE_MCP_TENANT_REQUESTS_PER_MINUTE,
        unavailable_detail="Workspace MCP rate limiter is unavailable",
        primary_limit_detail="Workspace MCP token rate limit exceeded",
        window_ttl=window_ttl,
        tenant_limit_detail="Workspace MCP tenant rate limit exceeded",
    )


async def enforce_research_request_limit(request: Request, identity: Any) -> None:
    """Limit research lifecycle traffic separately from per-tool metering."""

    bucket, window_ttl = _minute_window()
    await _enforce_dual_principal_limit(
        request,
        primary_key=(
            f"rate:mcp:research:key:"
            f"{getattr(identity, 'principal_id', None) or identity.product_key_id}:{bucket}"
        ),
        tenant_key=(f"rate:mcp:research:tenant:{identity.tenant_id}:{bucket}"),
        primary_limit=settings.RESEARCH_MCP_KEY_REQUESTS_PER_MINUTE,
        tenant_limit=settings.RESEARCH_MCP_TENANT_REQUESTS_PER_MINUTE,
        unavailable_detail="Research MCP rate limiter is unavailable",
        primary_limit_detail="Research MCP key request rate limit exceeded",
        tenant_limit_detail="Research MCP tenant request rate limit exceeded",
        window_ttl=window_ttl,
    )


def _declared_content_length(scope: Scope) -> int | None:
    declared: int | None = None
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() != b"content-length":
            continue
        try:
            value = int(raw_value.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return None
        if value < 0 or (declared is not None and declared != value):
            return None
        declared = value
    return declared


async def buffer_bounded_request(
    scope: Scope,
    receive: Receive,
    *,
    maximum_bytes: int,
) -> Receive:
    """Buffer one bounded HTTP request and return an ASGI replay receiver.

    Checking both Content-Length and actual chunks protects deployments where
    a reverse proxy is bypassed and also covers chunked requests. The replay
    receiver delegates after the buffered request so disconnect messages still
    reach the MCP SDK normally.
    """

    if maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be positive")

    declared = _declared_content_length(scope)
    if declared is not None and declared > maximum_bytes:
        raise MCPRequestBodyTooLarge

    buffered: deque[Message] = deque()
    received = 0
    while True:
        message = await receive()
        buffered.append(message)
        if message["type"] != "http.request":
            break
        received += len(message.get("body", b""))
        if received > maximum_bytes:
            raise MCPRequestBodyTooLarge
        if not message.get("more_body", False):
            break

    async def replay() -> Message:
        if buffered:
            return buffered.popleft()
        return await receive()

    return replay
