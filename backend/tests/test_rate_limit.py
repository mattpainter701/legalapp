import hashlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.middleware import rate_limit
from app.middleware.rate_limit import (
    AUTH_LIMITS,
    TENANT_DAILY_LIMITS,
    RateLimitMiddleware,
    _counts_against_tenant_daily,
)


class _FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds


def _platform_test_app(redis_client) -> FastAPI:
    app = FastAPI()
    app.state.redis = redis_client
    app.add_middleware(RateLimitMiddleware)

    @app.post("/api/platform/auth/token")
    async def exchange_bootstrap():
        return {"ok": True}

    @app.get("/api/platform/tenants")
    async def list_tenants():
        return {"ok": True}

    @app.post("/api/workspace-mcp/oauth/register")
    async def register_workspace_mcp_client():
        return {"ok": True}

    @app.post("/api/research-mcp/oauth/register")
    async def register_research_mcp_client():
        return {"ok": True}

    return app


def test_tenant_daily_limit_skips_conversation_reads_and_deletes():
    assert _counts_against_tenant_daily("GET", "/api/conversations") is False
    assert (
        _counts_against_tenant_daily(
            "GET", "/api/conversations/00000000-0000-0000-0000-000000000001"
        )
        is False
    )
    assert (
        _counts_against_tenant_daily(
            "DELETE", "/api/conversations/00000000-0000-0000-0000-000000000001"
        )
        is False
    )


def test_tenant_daily_limit_counts_llm_and_tool_paths():
    assert (
        _counts_against_tenant_daily(
            "POST",
            "/api/conversations/00000000-0000-0000-0000-000000000001/messages",
        )
        is True
    )
    assert (
        _counts_against_tenant_daily(
            "POST",
            "/api/conversations/00000000-0000-0000-0000-000000000001/messages/stream",
        )
        is True
    )
    assert _counts_against_tenant_daily("POST", "/api/plugins/execute") is True


def test_demo_has_explicit_public_and_daily_limits_without_changing_unknown_fallback():
    assert AUTH_LIMITS["/api/demo/session"] == (5, 900)
    assert AUTH_LIMITS["/api/workspace-mcp/oauth/register"] == (300, 3600)
    assert AUTH_LIMITS["/api/research-mcp/oauth/register"] == (300, 3600)
    assert TENANT_DAILY_LIMITS["demo"] == 200
    assert TENANT_DAILY_LIMITS["payg"] == 10_000


@pytest.mark.asyncio
async def test_platform_bootstrap_is_limited_by_source_ip(monkeypatch):
    redis_client = _FakeRedis()
    app = _platform_test_app(redis_client)
    monkeypatch.setattr(rate_limit.settings, "DEV_MODE", False)
    monkeypatch.setattr(
        rate_limit.settings, "PLATFORM_BOOTSTRAP_LIMIT_PER_5_MINUTES", 2
    )

    transport = ASGITransport(app=app, client=("203.0.113.50", 41234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/api/platform/auth/token")
        second = await client.post("/api/platform/auth/token")
        limited = await client.post("/api/platform/auth/token")

    assert first.status_code == second.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "300"
    key = "rate:platform:bootstrap:203.0.113.50"
    assert redis_client.counts[key] == 3
    assert redis_client.expirations[key] == 300


@pytest.mark.asyncio
async def test_platform_bearer_traffic_is_limited_per_session_token(monkeypatch):
    redis_client = _FakeRedis()
    app = _platform_test_app(redis_client)
    monkeypatch.setattr(rate_limit.settings, "DEV_MODE", False)
    monkeypatch.setattr(rate_limit.settings, "PLATFORM_RATE_LIMIT_PER_MINUTE", 1)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(
            "/api/platform/tenants", headers={"Authorization": "Bearer session-a"}
        )
        limited = await client.get(
            "/api/platform/tenants", headers={"Authorization": "Bearer session-a"}
        )
        separate_session = await client.get(
            "/api/platform/tenants", headers={"Authorization": "Bearer session-b"}
        )

    assert first.status_code == separate_session.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    digest_a = hashlib.sha256(b"Bearer session-a").hexdigest()[:32]
    digest_b = hashlib.sha256(b"Bearer session-b").hexdigest()[:32]
    assert redis_client.counts[f"rate:platform:token:{digest_a}"] == 2
    assert redis_client.counts[f"rate:platform:token:{digest_b}"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/workspace-mcp/oauth/register",
        "/api/research-mcp/oauth/register",
    ],
)
async def test_mcp_dynamic_registration_is_bounded_per_source_ip(monkeypatch, path):
    redis_client = _FakeRedis()
    app = _platform_test_app(redis_client)
    monkeypatch.setitem(AUTH_LIMITS, path, (1, 3600))

    transport = ASGITransport(app=app, client=("198.51.100.45", 41234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = await client.post(path)
        limited = await client.post(path)

    assert accepted.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "3600"
    key = f"rate:auth:{path}:198.51.100.45"
    assert redis_client.counts[key] == 2
    assert redis_client.expirations[key] == 3600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/platform/auth/token"),
        ("GET", "/api/platform/tenants"),
    ],
)
async def test_platform_limiter_fails_closed_without_redis_in_production(
    monkeypatch, method, path
):
    app = _platform_test_app(None)
    monkeypatch.setattr(rate_limit.settings, "DEV_MODE", False)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(
            method, path, headers={"Authorization": "Bearer session"}
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Platform rate limiter is unavailable"}
