import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import mcp
from app.routers.mcp import ToolCallRequest, _mcp_proxy_url


def test_mcp_proxy_url_joins_base_and_api_path():
    assert (
        _mcp_proxy_url("http://courtlistener-mcp:8021/", "/api/mcp/tools/call")
        == "http://courtlistener-mcp:8021/api/mcp/tools/call"
    )


@pytest.mark.asyncio
async def test_call_tool_authenticates_before_proxy(monkeypatch):
    request = SimpleNamespace(headers={})
    body = ToolCallRequest(
        name="search_caselaw",
        arguments={"query": "parental rights", "top_k": 1},
    )

    async def reject_identity(*args, **kwargs):
        raise HTTPException(status_code=401, detail="Not authenticated")

    async def proxy_should_not_run(*args, **kwargs):
        raise AssertionError("proxy should not run before auth")

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp, "_require_mcp_identity", reject_identity)
    monkeypatch.setattr(mcp, "_proxy_post", proxy_should_not_run)

    with pytest.raises(HTTPException) as exc:
        await mcp.call_tool(body, request, object())

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_call_tool_proxies_after_auth(monkeypatch):
    tenant_id = uuid.uuid4()
    request = SimpleNamespace(headers={"Authorization": "Bearer test"})
    body = ToolCallRequest(
        name="search_caselaw",
        arguments={"query": "bankruptcy exemption", "top_k": 2},
    )
    calls = []

    async def allow_identity(req, db):
        calls.append(("auth", req, db))
        return (
            SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="admin"),
            SimpleNamespace(id=tenant_id),
        )

    async def proxy(path, req, payload):
        calls.append(("proxy", path, req, payload))
        return {"content": [], "isError": False}

    async def record_usage(**kwargs):
        calls.append(("usage", kwargs))

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp, "_require_mcp_identity", allow_identity)
    monkeypatch.setattr(mcp, "_proxy_post", proxy)
    monkeypatch.setattr(mcp, "record_mcp_usage", record_usage)

    result = await mcp.call_tool(body, request, object())

    assert result == {"content": [], "isError": False}
    assert calls[0][0] == "auth"
    assert calls[1] == (
        "proxy",
        "/api/mcp/tools/call",
        request,
        {"name": "search_caselaw", "arguments": body.arguments},
    )
    assert calls[2][0] == "usage"


@pytest.mark.asyncio
async def test_proxied_tool_names_uses_live_manifest(monkeypatch):
    async def proxy_manifest(path, request):
        assert path == "/api/mcp"
        return {
            "tools": [
                {"name": "search_caselaw"},
                {"name": "get_case_details"},
                {"missing": "ignored"},
            ]
        }

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp, "_proxy_get", proxy_manifest)

    assert await mcp._proxied_tool_names(SimpleNamespace(headers={})) == [
        "search_caselaw",
        "get_case_details",
    ]


@pytest.mark.asyncio
async def test_api_key_lookup_sets_tenant_context_before_admin_query(monkeypatch):
    tenant_id = uuid.uuid4()
    tenant = SimpleNamespace(id=tenant_id)
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="admin")
    calls = []

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeDb:
        async def execute(self, statement):
            calls.append(("execute", len(calls), statement))
            if len([call for call in calls if call[0] == "execute"]) == 1:
                return Result(tenant)
            return Result(admin)

    async def record_tenant_context(db, context_tenant_id):
        calls.append(("context", context_tenant_id))

    monkeypatch.setattr(mcp, "set_tenant_context", record_tenant_context)

    user, resolved_tenant = await mcp._get_user_and_tenant(
        SimpleNamespace(headers={"X-API-Key": "raw-test-key"}),
        FakeDb(),
    )

    assert user is admin
    assert resolved_tenant is tenant
    assert [call[0] for call in calls] == ["execute", "context", "execute"]
    assert calls[1] == ("context", str(tenant_id))
