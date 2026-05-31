"""
Per-tenant + per-user rate limiting via Redis.
Nginx handles IP-level limiting; this layer adds per-tenant daily caps
and per-user hourly caps so no single user or firm can exhaust the system.

Limits:
  user:    200 requests / hour
  tenant:  flat tier  → 1 000 LLM calls / day
           payg tier  → 10 000 LLM calls / day  (they pay per call anyway)

Only /api/conversations and /api/plugins paths count against tenant daily limit.
"""

from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

settings = get_settings()

RATE_LIMITED_PREFIXES = ("/api/conversations", "/api/plugins")
SKIP_PREFIXES = (
    "/api/auth/",
    "/api/billing/webhook",
    "/health",
    "/docs",
    "/openapi.json",
)

TENANT_DAILY_LIMITS = {"flat": 1_000, "payg": 10_000}
USER_HOURLY_LIMIT = 200


def _current_hour_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year}{now.month:02d}{now.day:02d}{now.hour:02d}"


def _current_day_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year}{now.month:02d}{now.day:02d}"


def _extract_jwt_claims(request: Request) -> tuple[Optional[str], Optional[str], str]:
    """Return (user_id, tenant_id, billing_tier) from the JWT, or (None, None, 'payg')."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, None, "payg"
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload.get("sub"), payload.get("tenant_id"), payload.get("billing_tier", "payg")
    except JWTError:
        return None, None, "payg"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Reads Redis from request.app.state.redis at request time (set during lifespan)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        self._redis: aioredis.Redis | None = getattr(request.app.state, "redis", None)
        path = request.url.path

        # Skip non-rate-limited paths
        if any(path.startswith(p) for p in SKIP_PREFIXES):
            return await call_next(request)

        user_id, tenant_id, billing_tier = _extract_jwt_claims(request)

        # ── Per-user hourly limit ─────────────────────────────────────────────
        if user_id:
            user_key = f"rate:user:{user_id}:{_current_hour_key()}"
            try:
                count = await self._redis.incr(user_key)
                if count == 1:
                    await self._redis.expire(user_key, 3600)
                if count > USER_HOURLY_LIMIT:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Hourly request limit exceeded. Please retry in a few minutes."},
                        headers={"Retry-After": "60"},
                    )
            except aioredis.RedisError:
                pass  # Redis unavailable — fail open

        # ── Per-tenant daily limit (LLM-heavy paths only) ─────────────────────
        if tenant_id and any(path.startswith(p) for p in RATE_LIMITED_PREFIXES):
            daily_limit = TENANT_DAILY_LIMITS.get(billing_tier, TENANT_DAILY_LIMITS["payg"])
            tenant_key = f"rate:tenant:{tenant_id}:{_current_day_key()}"
            try:
                count = await self._redis.incr(tenant_key)
                if count == 1:
                    await self._redis.expire(tenant_key, 86400)
                if count > daily_limit:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": f"Daily query limit of {daily_limit} reached for your plan. Resets at midnight UTC."
                        },
                        headers={"Retry-After": "3600"},
                    )
            except aioredis.RedisError:
                pass

        return await call_next(request)
