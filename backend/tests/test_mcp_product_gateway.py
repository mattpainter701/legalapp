import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.mcp_product import MCPProductKey, MCPUsageEvent
from app.routers import mcp
from app.routers.mcp import ToolCallRequest
from app.services import mcp_product


def test_product_key_is_distinct_from_legacy_tenant_key():
    raw = mcp_product.generate_product_key()

    assert raw.startswith("clmcp_")
    assert len(mcp_product.hash_key(raw)) == 64
    assert mcp_product.mask_key(raw).startswith("clmcp_")


def test_product_key_scope_rejects_unknown_tools():
    with pytest.raises(HTTPException) as exc:
        mcp_product.normalize_allowed_tools(["search_caselaw", "not_a_tool"])

    assert exc.value.status_code == 400


def test_product_key_scope_defaults_to_all_tools():
    assert mcp_product.normalize_allowed_tools(None) == mcp_product.DEFAULT_ALLOWED_TOOLS
    assert mcp_product.normalize_allowed_tools([]) == mcp_product.DEFAULT_ALLOWED_TOOLS


def test_product_key_scope_accepts_expanded_courtlistener_tools():
    tools = mcp_product.normalize_allowed_tools(
        ["get_full_opinion", "find_similar_cases", "sync_status", "corpus_status"]
    )

    assert tools == ["get_full_opinion", "find_similar_cases", "sync_status", "corpus_status"]


@pytest.mark.asyncio
async def test_external_product_key_rejects_disallowed_tool_before_proxy(monkeypatch):
    tenant_id = uuid.uuid4()
    product_key = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        allowed_tools=["search_caselaw"],
        monthly_call_limit=None,
    )
    request = SimpleNamespace(
        headers={"X-MCP-API-Key": "clmcp_test"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    body = ToolCallRequest(name="get_case_details", arguments={"id": "abc"})

    async def resolve_key(*args, **kwargs):
        return product_key, SimpleNamespace(id=tenant_id)

    async def proxy_should_not_run(*args, **kwargs):
        raise AssertionError("proxy should not run when tool is outside key scope")

    async def set_context(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp, "resolve_product_key", resolve_key)
    monkeypatch.setattr(mcp, "set_tenant_context", set_context)
    monkeypatch.setattr(mcp, "_proxy_post", proxy_should_not_run)

    with pytest.raises(HTTPException) as exc:
        await mcp.call_tool(body, request, object())

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_external_product_key_records_usage_after_proxy(monkeypatch):
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    product_key = SimpleNamespace(
        id=key_id,
        tenant_id=tenant_id,
        allowed_tools=["search_caselaw"],
        monthly_call_limit=100,
    )
    request = SimpleNamespace(
        headers={"X-MCP-API-Key": "clmcp_test"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    body = ToolCallRequest(name="search_caselaw", arguments={"query": "oil", "top_k": 2})
    calls = []

    async def resolve_key(*args, **kwargs):
        return product_key, SimpleNamespace(id=tenant_id)

    async def quota_ok(*args, **kwargs):
        calls.append(("quota", args, kwargs))

    async def proxy(path, req, payload):
        return {"content": [{"type": "json", "json": {"results": [{}, {}]}}], "isError": False}

    async def record_usage(**kwargs):
        calls.append(("usage", kwargs))

    async def set_context(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp, "resolve_product_key", resolve_key)
    monkeypatch.setattr(mcp, "set_tenant_context", set_context)
    monkeypatch.setattr(mcp, "enforce_product_key_quota", quota_ok)
    monkeypatch.setattr(mcp, "_proxy_post", proxy)
    monkeypatch.setattr(mcp, "record_mcp_usage", record_usage)

    result = await mcp.call_tool(body, request, object())

    assert result["isError"] is False
    assert calls[0][0] == "quota"
    assert calls[1][0] == "usage"
    usage = calls[1][1]
    assert usage["tenant_id"] == tenant_id
    assert usage["product_key_id"] == key_id
    assert usage["auth_type"] == "product_key"
    assert usage["tool_name"] == "search_caselaw"
    assert usage["result_count"] == 2


@pytest.mark.asyncio
async def test_internal_chat_usage_logging_has_internal_auth_type(monkeypatch):
    calls = []

    async def record_usage(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(mcp_product, "record_mcp_usage", record_usage)

    await mcp_product.record_internal_chat_mcp_usage(
        db=object(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        tool_name="search_caselaw",
        status_code=200,
        result_count=4,
        latency_ms=42,
    )

    assert calls[0]["auth_type"] == "internal_chat"
    assert calls[0]["product_key_id"] is None


@pytest.mark.asyncio
async def test_product_key_usage_can_emit_stripe_meter_event(
    monkeypatch, db_session, test_tenant
):
    tenant_id = test_tenant.id
    test_tenant.stripe_customer_id = "cus_test123"
    await db_session.commit()
    calls = []

    class FakeMeterEvent:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(mcp_product.settings, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(
        mcp_product.settings,
        "STRIPE_MCP_METER_EVENT_NAME",
        "mcp_product_key_calls",
    )
    monkeypatch.setattr(
        mcp_product.stripe,
        "billing",
        SimpleNamespace(MeterEvent=FakeMeterEvent),
        raising=False,
    )
    key = MCPProductKey(
        tenant_id=tenant_id,
        name="Stripe meter key",
        key_hash=mcp_product.hash_key("clmcp_stripe_meter"),
        key_prefix="clmcp_strip",
        allowed_tools=None,
    )
    db_session.add(key)
    await db_session.flush()

    event = await mcp_product.record_mcp_usage(
        db=db_session,
        tenant_id=tenant_id,
        product_key_id=key.id,
        auth_type="product_key",
        transport="messages",
        tool_name="search_caselaw",
        status_code=200,
        result_count=2,
    )

    saved = (
        await db_session.execute(select(MCPUsageEvent).where(MCPUsageEvent.id == event.id))
    ).scalar_one()
    assert saved.tool_name == "search_caselaw"
    assert calls[0]["event_name"] == "mcp_product_key_calls"
    assert calls[0]["payload"] == {"stripe_customer_id": "cus_test123", "value": "1"}
    assert calls[0]["identifier"] == f"mcp_usage_{event.id}"


def test_mcp_messages_accepts_jsonrpc_tools_call_shape():
    body = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "tools/call",
        "params": {"name": "search_caselaw", "arguments": {"query": "tax"}},
    }

    parsed = mcp.parse_mcp_message_body(body)

    assert parsed.name == "search_caselaw"
    assert parsed.arguments == {"query": "tax"}


@pytest.mark.asyncio
async def test_tenant_admin_can_create_product_key(monkeypatch):
    tenant_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="admin")
    created_key = SimpleNamespace(
        id=uuid.uuid4(),
        name="Partner API",
        allowed_tools=["search_caselaw"],
        monthly_call_limit=250,
        is_active=True,
    )

    async def current_user(*args, **kwargs):
        return user

    async def create_key(*args, **kwargs):
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["user_id"] == user.id
        assert kwargs["name"] == "Partner API"
        assert kwargs["allowed_tools"] is None
        return created_key, "clmcp_rawsecret"

    monkeypatch.setattr(mcp, "get_current_user", current_user)
    monkeypatch.setattr(mcp, "create_product_key", create_key)

    result = await mcp.create_mcp_product_key(
        mcp.ProductKeyCreateRequest(
            name="Partner API",
            monthly_call_limit=250,
            allowed_tools=[],
        ),
        SimpleNamespace(headers={}),
        object(),
    )

    assert result["api_key"] == "clmcp_rawsecret"
    assert result["api_key_masked"].startswith("clmcp_")
    assert result["monthly_call_limit"] == 250


@pytest.mark.asyncio
async def test_tenant_admin_can_revoke_product_key(monkeypatch):
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="admin")
    calls = []

    async def current_user(*args, **kwargs):
        return user

    async def revoke_key(*args, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(mcp, "get_current_user", current_user)
    monkeypatch.setattr(mcp, "revoke_product_key", revoke_key)

    result = await mcp.revoke_mcp_product_key(key_id, SimpleNamespace(headers={}), object())

    assert result == {"revoked": True}
    assert calls[0] == {"tenant_id": tenant_id, "key_id": key_id}
