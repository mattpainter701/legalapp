from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError
from starlette.requests import Request

from app.services import mcp_transport_security as transport


def _request(*, redis=None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/mcp/workspace",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(redis=redis)),
    }

    async def receive():
        return {"type": "http.disconnect"}

    return Request(scope, receive)


def _identity(token_id="token-1", tenant_id="tenant-1"):
    return SimpleNamespace(token_id=token_id, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_declared_content_length_over_cap_is_rejected_before_reading():
    scope = {
        "type": "http",
        "headers": [(b"content-length", b"11")],
    }

    async def receive():
        raise AssertionError("the body should not be read after a declared oversize")

    with pytest.raises(transport.MCPRequestBodyTooLarge):
        await transport.buffer_bounded_request(scope, receive, maximum_bytes=10)


@pytest.mark.asyncio
async def test_chunked_body_over_cap_is_rejected():
    scope = {"type": "http", "headers": []}
    messages = iter(
        [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    with pytest.raises(transport.MCPRequestBodyTooLarge):
        await transport.buffer_bounded_request(scope, receive, maximum_bytes=7)


@pytest.mark.asyncio
async def test_exact_cap_body_is_replayed_without_loss():
    scope = {"type": "http", "headers": []}
    messages = iter(
        [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(messages)

    replay = await transport.buffer_bounded_request(scope, receive, maximum_bytes=8)
    assert await replay() == {
        "type": "http.request",
        "body": b"1234",
        "more_body": True,
    }
    assert await replay() == {
        "type": "http.request",
        "body": b"5678",
        "more_body": False,
    }
    assert await replay() == {"type": "http.disconnect"}


class _Redis:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def eval(self, script, key_count, token_key, tenant_key, *arguments):
        self.calls.append((script, key_count, token_key, tenant_key))
        self.arguments = arguments
        return self.result


@pytest.mark.asyncio
async def test_workspace_token_limit_returns_retry_after(monkeypatch):
    redis = _Redis([2, 41, 1, 55])
    monkeypatch.setattr(transport.settings, "DEV_MODE", False)
    monkeypatch.setattr(
        transport.settings, "WORKSPACE_MCP_TOKEN_REQUESTS_PER_MINUTE", 1
    )
    monkeypatch.setattr(
        transport.settings, "WORKSPACE_MCP_TENANT_REQUESTS_PER_MINUTE", 10
    )

    with pytest.raises(HTTPException) as exc_info:
        await transport.enforce_workspace_request_limit(
            _request(redis=redis), _identity()
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Workspace MCP token rate limit exceeded"
    assert exc_info.value.headers == {"Retry-After": "41"}
    assert redis.calls and redis.calls[0][1] == 2


@pytest.mark.asyncio
async def test_workspace_tenant_limit_returns_retry_after(monkeypatch):
    redis = _Redis([1, 55, 3, 37])
    monkeypatch.setattr(transport.settings, "DEV_MODE", False)
    monkeypatch.setattr(
        transport.settings, "WORKSPACE_MCP_TOKEN_REQUESTS_PER_MINUTE", 10
    )
    monkeypatch.setattr(
        transport.settings, "WORKSPACE_MCP_TENANT_REQUESTS_PER_MINUTE", 2
    )

    with pytest.raises(HTTPException) as exc_info:
        await transport.enforce_workspace_request_limit(
            _request(redis=redis), _identity(token_id="token-2", tenant_id="tenant-2")
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Workspace MCP tenant rate limit exceeded"
    assert exc_info.value.headers == {"Retry-After": "37"}


@pytest.mark.asyncio
async def test_dev_mode_uses_fallback_counters(monkeypatch):
    transport._fallback_rate_hits.clear()
    monkeypatch.setattr(transport.settings, "DEV_MODE", True)
    monkeypatch.setattr(
        transport.settings, "WORKSPACE_MCP_TOKEN_REQUESTS_PER_MINUTE", 1
    )
    monkeypatch.setattr(
        transport.settings, "WORKSPACE_MCP_TENANT_REQUESTS_PER_MINUTE", 10
    )
    request = _request(redis=None)

    await transport.enforce_workspace_request_limit(
        request, _identity(token_id="dev-token")
    )
    with pytest.raises(HTTPException) as exc_info:
        await transport.enforce_workspace_request_limit(
            request, _identity(token_id="dev-token")
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"]


@pytest.mark.asyncio
async def test_production_fails_closed_without_redis(monkeypatch):
    monkeypatch.setattr(transport.settings, "DEV_MODE", False)

    with pytest.raises(HTTPException) as exc_info:
        await transport.enforce_workspace_request_limit(
            _request(redis=None), _identity()
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Workspace MCP rate limiter is unavailable"


class _FailingRedis:
    async def eval(self, *args):
        raise RedisError("redis unavailable")


@pytest.mark.asyncio
async def test_production_fails_closed_when_redis_errors(monkeypatch):
    monkeypatch.setattr(transport.settings, "DEV_MODE", False)

    with pytest.raises(HTTPException) as exc_info:
        await transport.enforce_workspace_request_limit(
            _request(redis=_FailingRedis()), _identity()
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Workspace MCP rate limiter is unavailable"


@pytest.mark.asyncio
async def test_research_key_limit_is_separate_from_tool_metering(monkeypatch):
    redis = _Redis([3, 23, 1, 50])
    monkeypatch.setattr(transport.settings, "DEV_MODE", False)
    monkeypatch.setattr(transport.settings, "RESEARCH_MCP_KEY_REQUESTS_PER_MINUTE", 2)
    monkeypatch.setattr(
        transport.settings, "RESEARCH_MCP_TENANT_REQUESTS_PER_MINUTE", 20
    )
    identity = SimpleNamespace(product_key_id="key-1", tenant_id="tenant-1")

    with pytest.raises(HTTPException) as exc_info:
        await transport.enforce_research_request_limit(_request(redis=redis), identity)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Research MCP key request rate limit exceeded"
    assert exc_info.value.headers == {"Retry-After": "23"}
    _, key_count, primary_key, tenant_key = redis.calls[0]
    assert key_count == 2
    assert "rate:mcp:research:key:key-1:" in primary_key
    assert "rate:mcp:research:tenant:tenant-1:" in tenant_key


@pytest.mark.asyncio
async def test_research_tenant_limit_aggregates_product_keys(monkeypatch):
    redis = _Redis([1, 50, 5, 19])
    monkeypatch.setattr(transport.settings, "DEV_MODE", False)
    monkeypatch.setattr(transport.settings, "RESEARCH_MCP_KEY_REQUESTS_PER_MINUTE", 20)
    monkeypatch.setattr(
        transport.settings, "RESEARCH_MCP_TENANT_REQUESTS_PER_MINUTE", 4
    )
    identity = SimpleNamespace(product_key_id="key-2", tenant_id="tenant-1")

    with pytest.raises(HTTPException) as exc_info:
        await transport.enforce_research_request_limit(_request(redis=redis), identity)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Research MCP tenant request rate limit exceeded"
    assert exc_info.value.headers == {"Retry-After": "19"}


@pytest.mark.asyncio
async def test_malformed_redis_result_fails_closed_in_production(monkeypatch):
    monkeypatch.setattr(transport.settings, "DEV_MODE", False)

    with pytest.raises(HTTPException) as exc_info:
        await transport.enforce_workspace_request_limit(
            _request(redis=_Redis([1, 30])), _identity()
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Workspace MCP rate limiter is unavailable"


@pytest.mark.asyncio
async def test_non_positive_body_cap_is_rejected():
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    with pytest.raises(ValueError, match="maximum_bytes must be positive"):
        await transport.buffer_bounded_request(
            {"type": "http", "headers": []},
            receive,
            maximum_bytes=0,
        )
