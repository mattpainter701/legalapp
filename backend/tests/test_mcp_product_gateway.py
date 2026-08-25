import uuid
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

    assert raw.startswith("lhrk_")
    assert len(mcp_product.hash_key(raw)) == 64
    assert mcp_product.mask_key(raw).startswith("lhrk_")


def test_research_scope_excludes_workspace_tools_and_stale_keys_cannot_use_them():
    assert "list_matters" not in mcp_product.DEFAULT_ALLOWED_TOOLS
    assert "list_matter_documents" not in mcp_product.DEFAULT_ALLOWED_TOOLS
    assert "create_document" not in mcp_product.DEFAULT_ALLOWED_TOOLS

    stale_key = SimpleNamespace(allowed_tools=["list_matters"])
    with pytest.raises(HTTPException) as exc:
        mcp_product.ensure_tool_allowed(stale_key, "list_matters")

    assert exc.value.status_code == 403
    assert "Research MCP" in exc.value.detail


def test_product_key_scope_rejects_unknown_tools():
    with pytest.raises(HTTPException) as exc:
        mcp_product.normalize_allowed_tools(["search_caselaw", "not_a_tool"])

    assert exc.value.status_code == 400


def test_product_key_scope_defaults_to_all_tools():
    assert (
        mcp_product.normalize_allowed_tools(None) == mcp_product.DEFAULT_ALLOWED_TOOLS
    )
    assert mcp_product.normalize_allowed_tools([]) == mcp_product.DEFAULT_ALLOWED_TOOLS


def test_product_key_scope_accepts_expanded_courtlistener_tools():
    tools = mcp_product.normalize_allowed_tools(
        ["get_full_opinion", "find_similar_cases", "sync_status", "corpus_status"]
    )

    assert tools == [
        "get_full_opinion",
        "find_similar_cases",
        "sync_status",
        "corpus_status",
    ]


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
async def test_existing_key_with_workspace_tool_cannot_invoke_research_tool(
    monkeypatch,
):
    tenant_id = uuid.uuid4()
    product_key = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        allowed_tools=["list_matters"],
        monthly_call_limit=None,
    )
    request = SimpleNamespace(
        headers={"X-MCP-API-Key": "clmcp_legacy"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    body = ToolCallRequest(name="list_matters", arguments={})

    async def resolve_key(*args, **kwargs):
        return product_key, SimpleNamespace(id=tenant_id)

    async def proxy_should_not_run(*args, **kwargs):
        raise AssertionError("stale workspace tool must not reach the research proxy")

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
    body = ToolCallRequest(
        name="search_caselaw", arguments={"query": "oil", "top_k": 2}
    )
    calls = []

    async def resolve_key(*args, **kwargs):
        return product_key, SimpleNamespace(id=tenant_id)

    async def quota_ok(*args, **kwargs):
        calls.append(("quota", args, kwargs))

    async def burst_ok(*args, **kwargs):
        return None

    async def proxy(path, req, payload):
        return {
            "content": [{"type": "json", "json": {"results": [{}, {}]}}],
            "isError": False,
        }

    async def record_usage(**kwargs):
        calls.append(("usage", kwargs))

    async def set_context(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp, "resolve_product_key", resolve_key)
    monkeypatch.setattr(mcp, "set_tenant_context", set_context)
    monkeypatch.setattr(mcp, "enforce_product_key_burst_limit", burst_ok)
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
async def test_research_oauth_principal_uses_user_quota_and_usage_identity(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    tenant = SimpleNamespace(id=tenant_id)
    identity = SimpleNamespace(
        auth_type="research_oauth",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        oauth_grant_id=str(grant_id),
    )
    request = SimpleNamespace(
        headers={"User-Agent": "oauth-probe/1.0"},
        scope={"mcp_product_identity": identity},
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(redis=object())),
    )
    calls = []

    class DB:
        async def scalar(self, _query):
            return tenant

    async def set_context(*args, **kwargs):
        return None

    async def burst_ok(redis, principal):
        calls.append(("burst", redis, principal))

    async def quota_ok(db, **kwargs):
        calls.append(("quota", db, kwargs))

    async def proxy(*args, **kwargs):
        return {"content": [], "isError": False}

    async def record_usage(**kwargs):
        calls.append(("usage", kwargs))

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp, "set_tenant_context", set_context)
    monkeypatch.setattr(mcp, "ensure_mcp_product_access", lambda _: None)
    monkeypatch.setattr(mcp, "enforce_product_key_burst_limit", burst_ok)
    monkeypatch.setattr(mcp, "enforce_research_oauth_quota", quota_ok)
    monkeypatch.setattr(mcp, "_proxy_post", proxy)
    monkeypatch.setattr(mcp, "record_mcp_usage", record_usage)

    result = await mcp._call_tool_with_product_key(
        ToolCallRequest(name="search_caselaw", arguments={"query": "ada"}),
        request,
        DB(),
        transport="streamable_http",
    )

    assert result["isError"] is False
    assert calls[0][0] == "burst"
    assert calls[0][2].id == f"oauth-user:{tenant_id}:{user_id}"
    assert calls[1][0] == "quota"
    assert calls[1][2] == {"tenant_id": tenant_id, "user_id": user_id}
    usage = calls[2][1]
    assert usage["product_key_id"] is None
    assert usage["oauth_grant_id"] == grant_id
    assert usage["user_id"] == user_id
    assert usage["auth_type"] == "research_oauth"
    assert usage["transport"] == "streamable_http"


@pytest.mark.asyncio
async def test_research_oauth_principal_rejects_workspace_tool_before_proxy(
    monkeypatch,
):
    tenant_id = uuid.uuid4()
    identity = SimpleNamespace(
        auth_type="research_oauth",
        tenant_id=str(tenant_id),
        user_id=str(uuid.uuid4()),
        oauth_grant_id=str(uuid.uuid4()),
    )
    request = SimpleNamespace(
        headers={},
        scope={"mcp_product_identity": identity},
    )

    class DB:
        async def scalar(self, _query):
            return SimpleNamespace(id=tenant_id)

    async def set_context(*args, **kwargs):
        return None

    async def proxy_should_not_run(*args, **kwargs):
        raise AssertionError("Workspace tool must not reach the Research proxy")

    monkeypatch.setattr(mcp, "set_tenant_context", set_context)
    monkeypatch.setattr(mcp, "ensure_mcp_product_access", lambda _: None)
    monkeypatch.setattr(mcp, "_proxy_post", proxy_should_not_run)

    with pytest.raises(HTTPException) as caught:
        await mcp._call_tool_with_product_key(
            ToolCallRequest(name="list_matters", arguments={}), request, DB()
        )

    assert caught.value.status_code == 403
    assert "Research MCP" in caught.value.detail


class _UsageSessionContext:
    def __init__(self):
        self.session = SimpleNamespace(commits=0)

        async def commit():
            self.session.commits += 1

        self.session.commit = commit

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def _run_failed_product_proxy_call(monkeypatch, failure):
    """Exercise the current proxy failure seam without requiring a database."""
    tenant_id = uuid.uuid4()
    product_key = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        allowed_tools=["search_caselaw"],
        monthly_call_limit=None,
    )
    tenant = SimpleNamespace(id=tenant_id)
    recorded = []
    usage_context = _UsageSessionContext()

    async def resolve_key(*args, **kwargs):
        return product_key, tenant

    async def no_op(*args, **kwargs):
        return None

    async def proxy(*args, **kwargs):
        raise failure

    async def record_usage(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp, "resolve_product_key", resolve_key)
    monkeypatch.setattr(mcp, "set_tenant_context", no_op)
    monkeypatch.setattr(mcp, "enforce_product_key_burst_limit", no_op)
    monkeypatch.setattr(mcp, "enforce_product_key_quota", no_op)
    monkeypatch.setattr(mcp, "_proxy_post", proxy)
    monkeypatch.setattr(mcp, "record_mcp_usage", record_usage)
    monkeypatch.setattr(mcp, "async_session_maker", lambda: usage_context)

    request = SimpleNamespace(
        headers={"X-MCP-API-Key": "lhrk_test", "User-Agent": "probe/1.0"},
        client=SimpleNamespace(host="203.0.113.7"),
        app=SimpleNamespace(state=SimpleNamespace(redis=None)),
    )

    with pytest.raises(type(failure)) as caught:
        await mcp._call_tool_with_product_key(
            ToolCallRequest(name="search_caselaw", arguments={"query": "ada"}),
            request,
            object(),
        )

    assert caught.value is failure
    assert len(recorded) == 1
    assert recorded[0]["tenant_id"] == tenant_id
    assert recorded[0]["db"] is usage_context.session
    assert recorded[0]["product_key_id"] == product_key.id
    assert recorded[0]["tool_name"] == "search_caselaw"
    assert recorded[0]["status_code"] == (
        failure.status_code if isinstance(failure, HTTPException) else 500
    )
    assert recorded[0]["error_class"] == type(failure).__name__
    assert usage_context.session.commits == 1


@pytest.mark.asyncio
async def test_unexpected_proxy_failure_is_reraised_and_metered(monkeypatch):
    await _run_failed_product_proxy_call(monkeypatch, RuntimeError("upstream failed"))


@pytest.mark.asyncio
async def test_http_proxy_failure_preserves_status_and_is_metered(monkeypatch):
    await _run_failed_product_proxy_call(
        monkeypatch, HTTPException(status_code=404, detail="Matter not found")
    )


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
async def test_product_key_usage_enqueues_durable_stripe_meter_event(
    db_session, test_tenant
):
    tenant_id = test_tenant.id
    test_tenant.stripe_customer_id = "cus_test123"
    await db_session.commit()
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
        transport="streamable_http",
        tool_name="search_caselaw",
        status_code=200,
        result_count=2,
    )

    saved = (
        await db_session.execute(
            select(MCPUsageEvent).where(MCPUsageEvent.id == event.id)
        )
    ).scalar_one()
    assert saved.tool_name == "search_caselaw"
    from app.models.durable_job import DurableJob

    job = await db_session.scalar(
        select(DurableJob).where(
            DurableJob.tenant_id == tenant_id,
            DurableJob.kind == "mcp_stripe_meter",
            DurableJob.idempotency_key == str(event.id),
        )
    )
    assert job is not None
    assert job.payload["stripe_customer_id"] == "cus_test123"
    assert job.payload["identifier"] == f"mcp_usage_{event.id}"


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
async def test_source_health_sanitizes_operator_errors(monkeypatch):
    async def require_identity(request, db):
        return object(), object()

    async def proxy(path, request, payload):
        assert payload == {"name": "sync_status", "arguments": {}}
        return {
            "content": [
                {
                    "type": "json",
                    "json": {
                        "sources": [
                            {
                                "source_key": "courtlistener:ohio-caselaw",
                                "publisher": "CourtListener",
                                "source_type": "case_law",
                                "jurisdiction": "OH",
                                "coverage_start": "2015-01-01",
                                "coverage_end": "2026-07-30",
                                "last_successful_sync_at": "2026-07-31T10:00:00Z",
                                "item_count": 1200,
                                "chunk_count": 5000,
                                "embedded_chunk_count": 4900,
                                "current_error": "database password leaked here",
                            }
                        ],
                        "source_partitions": [
                            {
                                "source_key": "courtlistener:ohio-caselaw",
                                "partition_key": "ohio",
                                "status": "failed",
                                "last_error": "upstream token leaked here",
                            }
                        ],
                    },
                }
            ]
        }

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp, "_require_mcp_identity", require_identity)
    monkeypatch.setattr(mcp, "_proxy_post", proxy)

    result = await mcp.source_health(SimpleNamespace(headers={}), object())

    assert result["available"] is True
    assert result["status"] == "attention"
    assert result["sources"][0]["item_count"] == 1200
    assert "current_error" not in result["sources"][0]
    assert "last_error" not in result["partitions"][0]


@pytest.mark.asyncio
async def test_tenant_admin_can_create_product_key(monkeypatch):
    tenant_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="admin")
    created_key = SimpleNamespace(
        id=uuid.uuid4(),
        name="Partner API",
        allowed_tools=["search_caselaw"],
        monthly_call_limit=250,
        burst_limit_per_minute=30,
        is_active=True,
    )

    async def current_user(*args, **kwargs):
        return user

    async def create_key(*args, **kwargs):
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["user_id"] == user.id
        assert kwargs["name"] == "Partner API"
        assert kwargs["allowed_tools"] is None
        return created_key, "lhrk_rawsecret"

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

    assert result["api_key"] == "lhrk_rawsecret"
    assert result["api_key_masked"].startswith("lhrk_")
    assert result["monthly_call_limit"] == 250
    assert result["burst_limit_per_minute"] == 30


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

    result = await mcp.revoke_mcp_product_key(
        key_id, SimpleNamespace(headers={}), object()
    )

    assert result == {"revoked": True}
    assert calls[0] == {"tenant_id": tenant_id, "key_id": key_id}
