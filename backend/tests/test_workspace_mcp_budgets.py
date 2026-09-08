import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError
from starlette.requests import Request

from app.services import mcp_transport_security as transport
from app.services import workspace_mcp_budgets as budgets
from app.services import workspace_mcp_protocol as protocol
from app.services.automation_capabilities import CapabilityError


def request(redis=None):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/mcp/workspace",
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(redis=redis)),
        }
    )


def identity(grant="grant-1", tenant="tenant-1", token="token-1"):
    return SimpleNamespace(grant_id=grant, tenant_id=tenant, token_id=token)


@pytest.mark.asyncio
async def test_grant_budget_survives_token_rotation_and_isolates_tenants(monkeypatch):
    monkeypatch.setattr(transport.settings, "DEV_MODE", True)
    monkeypatch.setattr(budgets.settings, "WORKSPACE_MCP_GRANT_CALLS_PER_MINUTE", 1)
    transport._fallback_rate_hits.clear()
    await budgets.enforce_workspace_grant_call_budget(request(), identity())
    with pytest.raises(HTTPException) as exc:
        await budgets.enforce_workspace_grant_call_budget(
            request(), identity(token="fresh")
        )
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) > 0
    await budgets.enforce_workspace_grant_call_budget(
        request(), identity(grant="grant-2")
    )
    await budgets.enforce_workspace_grant_call_budget(
        request(), identity(tenant="tenant-2")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redis", [None, SimpleNamespace(eval=AsyncMock(side_effect=RedisError))]
)
async def test_grant_budget_fails_closed_without_shared_limiter(monkeypatch, redis):
    monkeypatch.setattr(transport.settings, "DEV_MODE", False)
    with pytest.raises(HTTPException) as exc:
        await budgets.enforce_workspace_grant_call_budget(request(redis), identity())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_shared_counter_uses_grant_not_token_and_expiring_window(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=[1, 17, 1, 17]))
    await budgets.enforce_workspace_grant_call_budget(request(redis), identity())
    args = redis.eval.call_args.args
    assert args[1] == 2
    assert "tenant-1:grant-1:" in args[2]
    assert "token-1" not in args[2]
    assert 1 <= args[-1] <= 60


@pytest.mark.asyncio
async def test_concurrent_grant_admission_uses_atomic_redis_budget(
    monkeypatch, test_redis
):
    monkeypatch.setattr(transport.settings, "DEV_MODE", False)
    monkeypatch.setattr(budgets.settings, "WORKSPACE_MCP_GRANT_CALLS_PER_MINUTE", 7)
    monkeypatch.setattr(budgets, "_minute_window", lambda: (123, 60))
    tenant, grant = str(uuid.uuid4()), str(uuid.uuid4())
    outcomes = await asyncio.gather(
        *(
            budgets.enforce_workspace_grant_call_budget(
                request(test_redis), identity(grant=grant, tenant=tenant, token=str(n))
            )
            for n in range(30)
        ),
        return_exceptions=True,
    )
    assert sum(item is None for item in outcomes) == 7
    refused = [item for item in outcomes if item is not None]
    assert all(
        isinstance(item, HTTPException) and item.status_code == 429 for item in refused
    )
    assert all(1 <= int(item.headers["Retry-After"]) <= 60 for item in refused)
    key = f"rate:mcp:workspace:grant:{tenant}:{grant}:123"
    assert int(await test_redis.get(key)) == 30
    assert 0 < await test_redis.ttl(key) <= 60


def test_read_budget_counts_utf8_and_both_mcp_representations(monkeypatch):
    payload = {"text": 'é"\\' * 100}
    actual = protocol._tool_success(payload)
    envelope = actual.model_dump(exclude_none=True, by_alias=True)
    size = len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(budgets.settings, "WORKSPACE_MCP_READ_RESULT_MAX_BYTES", size)
    assert budgets.bounded_workspace_read_result(payload) == size
    monkeypatch.setattr(
        budgets.settings, "WORKSPACE_MCP_READ_RESULT_MAX_BYTES", size - 1
    )
    with pytest.raises(CapabilityError) as exc:
        budgets.bounded_workspace_read_result(payload)
    assert exc.value.code == "result_size_exceeded"
    assert payload["text"] not in str(exc.value)


@pytest.mark.asyncio
async def test_protocol_returns_retry_guidance(monkeypatch):
    actor = protocol.WorkspaceMCPIdentity(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        client_id="test",
        grant_id=str(uuid.uuid4()),
        token_id="test",
        scopes=frozenset({"matters:read"}),
        app_capabilities=frozenset({"manage_matters"}),
    )
    monkeypatch.setattr(protocol, "_request_and_identity", lambda: (request(), actor))
    monkeypatch.setattr(
        protocol,
        "execute_workspace_capability",
        AsyncMock(
            side_effect=HTTPException(
                429, "Grant budget exceeded", headers={"Retry-After": "17"}
            )
        ),
    )
    result = await protocol.call_workspace_tool("find_matter", {"query": "test"})
    assert result.isError
    assert result.structuredContent["error"]["code"] == "rate_limited"
    assert result.structuredContent["error"]["retry_after_seconds"] == 17


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["allowed", "rate_limited", "oversized"])
async def test_dispatch_enforces_budget_before_handler_and_audits_reads(
    monkeypatch, outcome
):
    from app.services.chat_tools import handlers

    actor = protocol.WorkspaceMCPIdentity(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        client_id="test",
        grant_id=str(uuid.uuid4()),
        token_id="test",
        scopes=frozenset({"matters:read"}),
        app_capabilities=frozenset({"manage_matters"}),
    )
    sessions = []

    @asynccontextmanager
    async def session():
        db = AsyncMock()
        sessions.append(db)
        yield db

    monkeypatch.setattr(protocol, "async_session_maker", session)
    monkeypatch.setattr(protocol, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        protocol,
        "_load_workspace_actor",
        AsyncMock(
            return_value=(
                SimpleNamespace(id=actor.user_id, tenant_id=actor.tenant_id),
                actor.app_capabilities,
            )
        ),
    )
    limit = AsyncMock(
        side_effect=HTTPException(429, "budget") if outcome == "rate_limited" else None
    )
    monkeypatch.setattr(protocol, "enforce_workspace_grant_call_budget", limit)
    payload = {"text": "private" * (1000 if outcome == "oversized" else 1)}
    handler = AsyncMock(return_value=payload)
    monkeypatch.setattr(handlers, "find_matter", handler)
    audit = AsyncMock()
    monkeypatch.setattr(protocol, "append_workspace_mcp_audit", audit)
    monkeypatch.setattr(budgets.settings, "WORKSPACE_MCP_READ_RESULT_MAX_BYTES", 1024)

    async def execute():
        return await protocol.execute_workspace_capability(
            name="find_matter",
            arguments={"query": "test"},
            request=request(),
            identity=actor,
        )

    if outcome == "allowed":
        assert await execute() == payload
        assert audit.call_args.kwargs["metadata"]["result_bytes"] > 0
        assert audit.call_args.kwargs["event_type"] == "tool_called"
    else:
        with pytest.raises(
            HTTPException if outcome == "rate_limited" else CapabilityError
        ):
            await execute()
        assert audit.call_args.kwargs["event_type"] == "tool_call_refused"
        from app.services.workspace_mcp_oauth import _bounded_audit_metadata

        metadata = _bounded_audit_metadata(audit.call_args.kwargs["metadata"])
        assert metadata["failure_reason"] == (
            "429" if outcome == "rate_limited" else "result_size_exceeded"
        )
    assert handler.await_count == (0 if outcome == "rate_limited" else 1)
    sessions[0].commit.assert_not_awaited()
    sessions[0].rollback.assert_awaited()
    sessions[1].commit.assert_awaited_once()
    assert "private" not in str(audit.call_args)
