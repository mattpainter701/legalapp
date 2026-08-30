import uuid
from datetime import datetime, timedelta, timezone
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
        purpose=None,
        assigned_to_user_id=None,
        allowed_tools=["search_caselaw"],
        monthly_call_limit=250,
        monthly_budget_cents=None,
        unit_price_cents=45,
        burst_limit_per_minute=30,
        expires_at=None,
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


def test_product_key_status_distinguishes_active_expired_and_revoked():
    now = datetime.now(timezone.utc)
    active = SimpleNamespace(
        is_active=True, revoked_at=None, expires_at=now + timedelta(days=1)
    )
    expired = SimpleNamespace(
        is_active=True, revoked_at=None, expires_at=now - timedelta(seconds=1)
    )
    revoked = SimpleNamespace(is_active=False, revoked_at=now, expires_at=None)

    assert mcp_product.product_key_status(active, now=now) == "active"
    assert mcp_product.product_key_status(expired, now=now) == "expired"
    assert mcp_product.product_key_status(revoked, now=now) == "revoked"


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


@pytest.mark.asyncio
async def test_tenant_admin_can_update_key_controls_and_clear_expiration(monkeypatch):
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="admin")
    calls = []

    async def current_user(*args, **kwargs):
        return user

    async def update_key(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            id=key_id,
            is_active=True,
            revoked_at=None,
            expires_at=None,
        )

    monkeypatch.setattr(mcp, "get_current_user", current_user)
    monkeypatch.setattr(mcp, "update_product_key", update_key)

    result = await mcp.update_mcp_product_key(
        key_id,
        mcp.ProductKeyUpdateRequest(
            monthly_budget_cents=9000,
            assigned_to_user_id=None,
            expires_at=None,
        ),
        SimpleNamespace(headers={}),
        object(),
    )

    assert result == {"updated": True, "id": str(key_id), "status": "active"}
    assert calls[0]["tenant_id"] == tenant_id
    assert calls[0]["key_id"] == key_id
    assert calls[0]["changes"] == {
        "monthly_budget_cents": 9000,
        "assigned_to_user_id": None,
        "expires_at": None,
    }


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"monthly_call_limit": 0}, "Invalid monthly MCP call limit"),
        ({"burst_limit_per_minute": 0}, "Invalid MCP burst limit"),
        ({"unit_price_cents": 0}, "MCP product price is invalid"),
        ({"monthly_budget_cents": True}, "Invalid monthly MCP budget"),
        (
            {"monthly_budget_cents": 44},
            "Monthly budget must cover at least one successful MCP call",
        ),
        (
            {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
            "Key expiration must be in the future",
        ),
    ],
)
def test_product_key_control_validation_rejects_invalid_boundaries(overrides, detail):
    controls = {
        "monthly_call_limit": 100,
        "burst_limit_per_minute": 10,
        "monthly_budget_cents": None,
        "unit_price_cents": 45,
        "expires_at": None,
    }
    controls.update(overrides)

    with pytest.raises(HTTPException) as exc:
        mcp_product._validate_key_controls(**controls)

    assert exc.value.status_code in {400, 503}
    assert exc.value.detail == detail


@pytest.mark.asyncio
async def test_product_key_assignee_validation_allows_unassigned_and_rejects_missing():
    class MissingAssigneeDB:
        async def scalar(self, _statement):
            return None

    db = MissingAssigneeDB()
    tenant_id = uuid.uuid4()

    await mcp_product._validate_assignee(
        db, tenant_id=tenant_id, assigned_to_user_id=None
    )
    with pytest.raises(HTTPException) as exc:
        await mcp_product._validate_assignee(
            db, tenant_id=tenant_id, assigned_to_user_id=uuid.uuid4()
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Assigned staff member is unavailable"


@pytest.mark.asyncio
async def test_create_product_key_normalizes_controls_and_snapshots_price(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    assigned_id = uuid.uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    tenant = SimpleNamespace(id=tenant_id)

    class CreateDB:
        def __init__(self):
            self.added = None
            self.committed = False

        async def scalar(self, _statement):
            return tenant

        def add(self, value):
            self.added = value

        async def commit(self):
            self.committed = True

        async def refresh(self, value):
            value.id = uuid.uuid4()

    async def validate_assignee(_db, **kwargs):
        assert kwargs == {
            "tenant_id": tenant_id,
            "assigned_to_user_id": assigned_id,
        }

    db = CreateDB()
    monkeypatch.setattr(mcp_product, "ensure_mcp_product_access", lambda _tenant: None)
    monkeypatch.setattr(mcp_product, "_validate_assignee", validate_assignee)
    monkeypatch.setattr(mcp_product.settings, "MCP_PRODUCT_CALL_PRICE_CENTS", 45)

    key, raw_key = await mcp_product.create_product_key(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        name="  Research team  ",
        purpose="  Public authority lookup  ",
        assigned_to_user_id=assigned_id,
        monthly_call_limit=500,
        monthly_budget_cents=4500,
        burst_limit_per_minute=25,
        expires_at=expires_at,
        allowed_tools=["search_caselaw"],
    )

    assert raw_key.startswith("lhrk_")
    assert db.added is key
    assert db.committed is True
    assert key.name == "Research team"
    assert key.purpose == "Public authority lookup"
    assert key.assigned_to_user_id == assigned_id
    assert key.allowed_tools == ["search_caselaw"]
    assert key.monthly_budget_cents == 4500
    assert key.unit_price_cents == 45
    assert key.expires_at == expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tenant", "name", "purpose", "detail"),
    [
        (None, "key", None, "Tenant not found"),
        (SimpleNamespace(), "x" * 121, None, "Key name is too long"),
        (SimpleNamespace(), "key", "x" * 256, "Key purpose is too long"),
    ],
)
async def test_create_product_key_rejects_missing_tenant_or_long_labels(
    monkeypatch, tenant, name, purpose, detail
):
    class ScalarDB:
        async def scalar(self, _statement):
            return tenant

    async def validate_assignee(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mcp_product, "ensure_mcp_product_access", lambda _tenant: None)
    monkeypatch.setattr(mcp_product, "_validate_assignee", validate_assignee)

    with pytest.raises(HTTPException) as exc:
        await mcp_product.create_product_key(
            ScalarDB(),
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name=name,
            purpose=purpose,
        )

    assert exc.value.detail == detail


@pytest.mark.asyncio
async def test_update_product_key_applies_editable_controls(monkeypatch):
    tenant_id = uuid.uuid4()
    assigned_id = uuid.uuid4()
    key = SimpleNamespace(
        id=uuid.uuid4(),
        assigned_to_user_id=None,
        monthly_call_limit=100,
        monthly_budget_cents=None,
        unit_price_cents=45,
        burst_limit_per_minute=10,
        expires_at=None,
        is_active=True,
        revoked_at=None,
    )

    class UpdateDB:
        committed = False

        async def scalar(self, _statement):
            return key

        async def commit(self):
            self.committed = True

        async def refresh(self, _value):
            return None

    async def validate_assignee(_db, **kwargs):
        assert kwargs["assigned_to_user_id"] == assigned_id

    db = UpdateDB()
    monkeypatch.setattr(mcp_product, "_validate_assignee", validate_assignee)
    updated = await mcp_product.update_product_key(
        db,
        tenant_id=tenant_id,
        key_id=key.id,
        changes={
            "name": "  Staff research  ",
            "purpose": "  Litigation  ",
            "assigned_to_user_id": assigned_id,
            "monthly_call_limit": 200,
            "monthly_budget_cents": 9000,
            "burst_limit_per_minute": 20,
            "allowed_tools": ["search_caselaw"],
        },
    )

    assert updated is key
    assert db.committed is True
    assert key.name == "Staff research"
    assert key.purpose == "Litigation"
    assert key.assigned_to_user_id == assigned_id
    assert key.allowed_tools == ["search_caselaw"]
    assert key.monthly_budget_cents == 9000


@pytest.mark.asyncio
async def test_update_product_key_handles_missing_revoked_and_invalid_labels(
    monkeypatch,
):
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()

    class ScalarDB:
        def __init__(self, value):
            self.value = value

        async def scalar(self, _statement):
            return self.value

    assert (
        await mcp_product.update_product_key(
            ScalarDB(None), tenant_id=tenant_id, key_id=key_id, changes={"name": "x"}
        )
        is None
    )

    revoked = SimpleNamespace(is_active=False, revoked_at=datetime.now(timezone.utc))
    with pytest.raises(HTTPException) as revoked_exc:
        await mcp_product.update_product_key(
            ScalarDB(revoked),
            tenant_id=tenant_id,
            key_id=key_id,
            changes={"name": "x"},
        )
    assert revoked_exc.value.status_code == 409

    editable = SimpleNamespace(
        assigned_to_user_id=None,
        monthly_call_limit=100,
        monthly_budget_cents=None,
        unit_price_cents=45,
        burst_limit_per_minute=10,
        expires_at=None,
        is_active=True,
        revoked_at=None,
    )

    async def validate_assignee(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mcp_product, "_validate_assignee", validate_assignee)
    for changes, detail in [
        ({"name": " "}, "Key name is required"),
        ({"name": "x" * 121}, "Key name is too long"),
        ({"purpose": "x" * 256}, "Key purpose is too long"),
    ]:
        with pytest.raises(HTTPException) as exc:
            await mcp_product.update_product_key(
                ScalarDB(editable),
                tenant_id=tenant_id,
                key_id=key_id,
                changes=changes,
            )
        assert exc.value.detail == detail


@pytest.mark.asyncio
async def test_monthly_key_usage_separates_failures_and_calculates_charges():
    key_id = uuid.uuid4()

    class Rows:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class UsageDB:
        def __init__(self):
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return Rows([(key_id, 200, 3, 8), (key_id, 500, 2, 0)])
            return Rows([(key_id, 45)])

    summary = await mcp_product.monthly_key_usage(UsageDB(), uuid.uuid4())

    assert summary[str(key_id)] == {
        "calls": 5,
        "successful_calls": 3,
        "failed_calls": 2,
        "results": 8,
        "charge_cents": 135,
        "charge_usd": 1.35,
    }


def test_product_key_header_rejects_duplicate_credentials():
    request = SimpleNamespace(
        headers={
            "X-MCP-API-Key": "lhrk_header",
            "Authorization": "Bearer lhrk_bearer",
        }
    )

    with pytest.raises(HTTPException) as exc:
        mcp._product_key_credential(request)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Multiple MCP credentials supplied"
