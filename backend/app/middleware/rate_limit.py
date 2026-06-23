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
import time
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

settings = get_settings()

RATE_LIMITED_PREFIXES = ("/api/conversations", "/api/plugins")
AUTH_LIMITS = {
    "/api/auth/login": (10, 600),
    "/api/auth/register": (5, 600),
    "/api/auth/forgot-password": (5, 900),
    "/api/auth/reset-password": (5, 900),
    # SMB agent registration is unauthenticated; rate-limit by IP to slow
    # brute-force of active pairing codes.
    "/api/v1/smb/agents/register": (5, 300),
}
SKIP_PREFIXES = (
    "/api/auth/",
    "/api/billing/webhook",
    "/api/integrations/zoom-phone/webhook",
    "/api/platform/",  # platform auth is key-based, not JWT
    "/health",
    "/docs",
    "/openapi.json",
)

TENANT_DAILY_LIMITS = {"flat": 1_000, "payg": 10_000}
USER_HOURLY_LIMIT = 600

# Cheap, high-frequency read endpoints that the SPA polls in the background.
# These must NOT count against the per-user hourly budget — a single dashboard
# left open would otherwise exhaust it (e.g. the call feed polls every 30s) and
# 429 every *other* request the user makes, including the admin tab. nginx still
# rate-limits these by IP. Matched as path prefixes.
USER_HOURLY_EXEMPT_PREFIXES = ("/api/intake/dashboard/recent-callers",)
_fallback_auth_hits: dict[str, tuple[int, float]] = {}


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
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return (
            payload.get("sub"),
            payload.get("tenant_id"),
            payload.get("billing_tier", "payg"),
        )
    except JWTError:
        return None, None, "payg"


def _client_ip(request: Request) -> str:
    """Resolve the real client IP, resistant to X-Forwarded-For spoofing.

    X-Forwarded-For is a left-to-right chain: each proxy *appends* the address
    it received the request from. The LEFTMOST entries are therefore fully
    client-controlled (a caller can send `X-Forwarded-For: 1.2.3.4` and it will
    sit at the head of the list), so trusting the first entry lets an attacker
    forge any IP and bypass per-IP rate limits.

    Only the rightmost entries — those appended by OUR own infrastructure — are
    trustworthy. With ``N = settings.TRUSTED_PROXY_HOPS`` trusted proxies in
    front of the app, the genuine client IP is the entry ``N`` positions from
    the RIGHT of the list (``xff[-N]``). TRUSTED_PROXY_HOPS MUST match the
    actual number of reverse proxies between the public internet and this app
    (e.g. 1 for a single nginx hop); too small and a spoofed value leaks
    through, too large and a real proxy IP is used instead of the client.

    Falls back to ``request.client.host`` (the immediate peer) when the header
    is absent, empty, malformed, or shorter than the trusted hop count.
    """
    peer = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return peer

    hops = settings.TRUSTED_PROXY_HOPS
    parts = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
    if hops >= 1 and len(parts) >= hops:
        return parts[-hops]
    return peer


def _fallback_auth_increment(key: str, window_seconds: int) -> int:
    now = time.time()
    expired = [
        hit_key
        for hit_key, (_, expires_at) in _fallback_auth_hits.items()
        if expires_at <= now
    ]
    for hit_key in expired:
        _fallback_auth_hits.pop(hit_key, None)

    count, expires_at = _fallback_auth_hits.get(key, (0, now + window_seconds))
    if expires_at <= now:
        count, expires_at = 0, now + window_seconds
    count += 1
    _fallback_auth_hits[key] = (count, expires_at)
    return count


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Reads Redis from request.app.state.redis at request time (set during lifespan)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        self._redis: aioredis.Redis | None = getattr(request.app.state, "redis", None)
        path = request.url.path

        if request.method == "POST":
            for auth_path, (limit, window_seconds) in AUTH_LIMITS.items():
                if path == auth_path:
                    key = f"rate:auth:{auth_path}:{_client_ip(request)}"
                    try:
                        if self._redis:
                            count = await self._redis.incr(key)
                            if count == 1:
                                await self._redis.expire(key, window_seconds)
                        else:
                            count = _fallback_auth_increment(key, window_seconds)
                    except aioredis.RedisError:
                        count = _fallback_auth_increment(key, window_seconds)

                    if count > limit:
                        return JSONResponse(
                            status_code=429,
                            content={
                                "detail": "Authentication rate limit exceeded. Please retry later."
                            },
                            headers={"Retry-After": str(window_seconds)},
                        )
                    break

        # Skip non-rate-limited paths
        if any(path.startswith(p) for p in SKIP_PREFIXES):
            return await call_next(request)

        user_id, tenant_id, billing_tier = _extract_jwt_claims(request)

        # ── Per-user hourly limit ─────────────────────────────────────────────
        exempt_from_user_limit = any(
            path.startswith(p) for p in USER_HOURLY_EXEMPT_PREFIXES
        )
        if user_id and not exempt_from_user_limit:
            user_key = f"rate:user:{user_id}:{_current_hour_key()}"
            try:
                if self._redis:
                    count = await self._redis.incr(user_key)
                    if count == 1:
                        await self._redis.expire(user_key, 3600)
                    if count > USER_HOURLY_LIMIT:
                        return JSONResponse(
                            status_code=429,
                            content={
                                "detail": "Hourly request limit exceeded. Please retry in a few minutes."
                            },
                            headers={"Retry-After": "60"},
                        )
            except aioredis.RedisError:
                pass  # Redis unavailable — fail open

        # ── Per-tenant daily limit (LLM-heavy paths only) ─────────────────────
        if tenant_id and any(path.startswith(p) for p in RATE_LIMITED_PREFIXES):
            daily_limit = TENANT_DAILY_LIMITS.get(
                billing_tier, TENANT_DAILY_LIMITS["payg"]
            )
            tenant_key = f"rate:tenant:{tenant_id}:{_current_day_key()}"
            try:
                if self._redis:
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
