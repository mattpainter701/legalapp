import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import mcp
from app.routers.mcp import ToolCallRequest, _mcp_proxy_url
from app.services import mcp_protocol


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
    request = SimpleNamespace(
        headers={"Authorization": "Bearer test"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
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
async def test_manifest_fails_closed_when_upstream_unavailable(monkeypatch):
    async def proxy_unavailable(path, request):
        raise RuntimeError("dns unavailable")

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp.settings, "BACKEND_URL", "https://legalapp.example")
    monkeypatch.setattr(mcp.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(mcp, "_proxy_get", proxy_unavailable)
    mcp_protocol.clear_tool_catalog_cache()
    try:
        with pytest.raises(HTTPException) as exc:
            await mcp.mcp_manifest(SimpleNamespace(headers={}))
    finally:
        mcp_protocol.clear_tool_catalog_cache()

    assert exc.value.status_code == 503
    assert exc.value.detail == "MCP tool catalog is unavailable"


@pytest.mark.asyncio
async def test_public_manifest_never_leaks_private_protocol_claim(monkeypatch):
    async def private_manifest(path, request):
        assert path == "/api/mcp"
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "private-courtlistener", "version": "0.1.0"},
            "tools": [
                {
                    "name": name,
                    "description": f"Private authenticated definition for {name}.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                }
                for name in mcp.DEFAULT_ALLOWED_TOOLS
            ],
        }

    monkeypatch.setattr(mcp.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(mcp.settings, "BACKEND_URL", "https://legalapp.example")
    monkeypatch.setattr(mcp.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(mcp, "_proxy_get", private_manifest)
    mcp_protocol.clear_tool_catalog_cache()
    try:
        manifest = await mcp.mcp_manifest(SimpleNamespace(headers={}))
    finally:
        mcp_protocol.clear_tool_catalog_cache()

    assert manifest["protocolVersion"] == "2025-06-18"
    assert manifest["serverInfo"]["name"] == "clarity-legal"
    assert (
        manifest["transports"]["streamable_http"] == "https://legalapp.example/api/mcp"
    )
    assert {tool["name"] for tool in manifest["tools"]} == set(
        mcp.DEFAULT_ALLOWED_TOOLS
    )


@pytest.mark.asyncio
async def test_legacy_api_key_is_rejected_without_database_lookup():
    with pytest.raises(HTTPException) as exc:
        await mcp._get_user_and_tenant(
            SimpleNamespace(headers={"X-API-Key": "raw-test-key"}),
            object(),
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "expected_detail"),
    [
        (mcp.get_api_key_info, "Legacy MCP API keys are retired"),
        (mcp.regenerate_api_key, "Legacy MCP API key issuance is retired"),
    ],
)
async def test_authenticated_legacy_api_key_routes_are_retired(
    monkeypatch, handler, expected_detail
):
    request = SimpleNamespace(headers={"Authorization": "Bearer current-session"})
    db = object()
    authenticated_user = SimpleNamespace(id=uuid.uuid4())
    calls = []

    async def allow_current_user(resolved_request, resolved_db):
        calls.append((resolved_request, resolved_db))
        return authenticated_user

    monkeypatch.setattr(mcp, "get_current_user", allow_current_user)

    with pytest.raises(HTTPException) as exc:
        await handler(request=request, db=db)

    assert calls == [(request, db)]
    assert exc.value.status_code == 410
    assert expected_detail in exc.value.detail
