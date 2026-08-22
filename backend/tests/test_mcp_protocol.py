from __future__ import annotations

import asyncio
import time
import uuid

import mcp.types as mcp_types
import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.requests import Request
from starlette.applications import Starlette
from starlette.routing import Route

from app.services import mcp_protocol


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def running_protocol_manager():
    started = asyncio.Event()
    stop = asyncio.Event()

    async def run_manager():
        async with mcp_protocol.protocol_lifespan():
            started.set()
            await stop.wait()

    task = asyncio.create_task(run_manager())
    await started.wait()
    yield
    stop.set()
    await task


@pytest.fixture
def protocol_app(monkeypatch):
    # The protocol tests deliberately use an in-process localhost client. Do
    # not inherit a developer machine's production BACKEND_URL into the SDK's
    # DNS-rebinding allow-list.
    monkeypatch.setattr(mcp_protocol.settings, "BACKEND_URL", "http://localhost:8000")
    monkeypatch.setattr(
        mcp_protocol.protocol_session_manager,
        "security_settings",
        mcp_protocol._transport_security(),
    )
    return Starlette(
        routes=[
            Route(
                mcp_protocol.MCP_ENDPOINT_PATH,
                endpoint=mcp_protocol.protocol_endpoint,
                methods=["GET", "POST", "DELETE"],
            )
        ]
    )


def test_transport_security_allows_canonical_research_host(monkeypatch):
    monkeypatch.setattr(
        mcp_protocol.settings,
        "RESEARCH_MCP_PUBLIC_URL",
        "https://research.getlawhand.com/api/mcp",
    )

    security = mcp_protocol._transport_security()

    expected_hosts = {"research.getlawhand.com"}
    expected_origins = {"https://research.getlawhand.com"}
    assert set(security.allowed_hosts) & expected_hosts == expected_hosts
    assert set(security.allowed_origins) & expected_origins == expected_origins


def _identity(*tools: str) -> mcp_protocol.MCPProductIdentity:
    return mcp_protocol.MCPProductIdentity(
        product_key_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        allowed_tools=frozenset(tools),
    )


def _test_catalog() -> tuple[mcp_types.Tool, ...]:
    return (
        mcp_types.Tool(
            name="search_caselaw",
            description="Search the authenticated upstream case-law corpus.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
            annotations=mcp_types.ToolAnnotations(readOnlyHint=True),
        ),
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": mcp_protocol.MCP_ENDPOINT_PATH,
            "raw_path": mcp_protocol.MCP_ENDPOINT_PATH.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"localhost:8000")],
            "client": ("127.0.0.1", 1234),
            "server": ("localhost", 8000),
        }
    )


def _protocol_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "X-MCP-API-Key": "clmcp_test",
    }


async def _allow_identity(monkeypatch, *tools: str) -> None:
    identity = _identity(*tools)

    async def authenticate(scope):
        return identity

    async def catalog(request):
        return _test_catalog()

    monkeypatch.setattr(mcp_protocol, "authenticate_product_request", authenticate)
    monkeypatch.setattr(mcp_protocol, "get_tool_catalog", catalog)
    monkeypatch.setattr(mcp_protocol.settings, "MCP_PRODUCT_ENABLED", True)


def _complete_upstream_manifest() -> dict:
    tools = []
    for name in mcp_protocol.DEFAULT_ALLOWED_TOOLS:
        schema = {"type": "object", "properties": {}, "required": []}
        if name == "search_caselaw":
            schema = _test_catalog()[0].inputSchema
        tools.append(
            {
                "name": name,
                "description": f"Authenticated upstream definition for {name}.",
                "inputSchema": schema,
            }
        )
    return {"tools": tools}


def test_upstream_catalog_requires_complete_real_contract():
    manifest = _complete_upstream_manifest()
    validated = mcp_protocol._validate_upstream_catalog(manifest)

    assert {tool.name for tool in validated} == set(mcp_protocol.DEFAULT_ALLOWED_TOOLS)
    search = next(tool for tool in validated if tool.name == "search_caselaw")
    assert search.inputSchema["properties"]["top_k"]["maximum"] == 50

    manifest["tools"].pop()
    with pytest.raises(ValueError, match="missing product tools"):
        mcp_protocol._validate_upstream_catalog(manifest)


@pytest.mark.asyncio
async def test_upstream_catalog_cache_has_bounded_ttl(monkeypatch):
    calls = 0
    tools = mcp_protocol._validate_upstream_catalog(_complete_upstream_manifest())

    async def load(request):
        nonlocal calls
        calls += 1
        return tools

    monkeypatch.setattr(mcp_protocol, "_load_upstream_tool_catalog", load)
    monkeypatch.setattr(
        mcp_protocol.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021"
    )
    mcp_protocol.clear_tool_catalog_cache()
    started = time.monotonic()
    try:
        first = await mcp_protocol.get_tool_catalog(_request())
        second = await mcp_protocol.get_tool_catalog(_request())
        expires_at, source, cached = mcp_protocol._tool_catalog_cache
    finally:
        mcp_protocol.clear_tool_catalog_cache()

    assert calls == 1
    assert first is second is cached
    assert source == "http://courtlistener-mcp:8021"
    assert 0 < expires_at - started <= mcp_protocol.TOOL_CATALOG_TTL_SECONDS + 1


@pytest.mark.asyncio
async def test_upstream_catalog_failure_is_fail_closed(monkeypatch):
    from app.routers import mcp

    async def unavailable(path, request):
        raise RuntimeError("private DNS unavailable")

    monkeypatch.setattr(mcp, "_proxy_get", unavailable)
    monkeypatch.setattr(
        mcp_protocol.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021"
    )
    mcp_protocol.clear_tool_catalog_cache()

    with pytest.raises(HTTPException) as exc:
        await mcp_protocol._load_upstream_tool_catalog(_request())

    assert exc.value.status_code == 503
    assert exc.value.detail == "MCP tool catalog is unavailable"


@pytest.mark.asyncio
async def test_tools_list_does_not_fall_back_when_catalog_is_unavailable(
    monkeypatch, protocol_app
):
    await _allow_identity(monkeypatch, "search_caselaw")

    async def unavailable(request):
        raise HTTPException(status_code=503, detail="MCP tool catalog is unavailable")

    monkeypatch.setattr(mcp_protocol, "get_tool_catalog", unavailable)
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            mcp_protocol.MCP_ENDPOINT_PATH,
            headers={
                **_protocol_headers(),
                "Mcp-Protocol-Version": mcp_protocol.MCP_PROTOCOL_VERSION,
            },
            json={"jsonrpc": "2.0", "id": 20, "method": "tools/list"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 20
    assert "error" in body
    assert "result" not in body


@pytest.mark.asyncio
async def test_protocol_get_is_not_a_legacy_manifest_or_sse_shim(
    monkeypatch, protocol_app
):
    await _allow_identity(monkeypatch, "search_caselaw")
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.get(
            mcp_protocol.MCP_ENDPOINT_PATH,
            headers={
                # A real Streamable HTTP GET with text/event-stream stays open;
                # request JSON only so this assertion can verify the endpoint
                # rejects legacy manifest semantics without opening a stream.
                "Accept": "application/json",
                "X-MCP-API-Key": "clmcp_test",
                "Mcp-Protocol-Version": mcp_protocol.MCP_PROTOCOL_VERSION,
            },
        )

    assert response.status_code == 406
    assert "tools" not in response.text
    assert "event: endpoint" not in response.text


@pytest.mark.asyncio
async def test_protocol_is_fail_closed_by_default(monkeypatch, protocol_app):
    monkeypatch.setattr(mcp_protocol.settings, "MCP_PRODUCT_ENABLED", False)
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            mcp_protocol.MCP_ENDPOINT_PATH,
            headers=_protocol_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": mcp_protocol.MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "MCP product endpoint is disabled"}


@pytest.mark.asyncio
async def test_protocol_requires_product_key(monkeypatch, protocol_app):
    monkeypatch.setattr(mcp_protocol.settings, "MCP_PRODUCT_ENABLED", True)
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            mcp_protocol.MCP_ENDPOINT_PATH,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": mcp_protocol.MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "MCP API key required"}


@pytest.mark.asyncio
async def test_official_client_initializes_and_discovers_scoped_tools(
    monkeypatch, protocol_app
):
    await _allow_identity(monkeypatch, "search_caselaw")

    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
        headers={"X-MCP-API-Key": "clmcp_test"},
    ) as http_client:
        async with streamable_http_client(
            "http://localhost:8000/api/mcp", http_client=http_client
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                discovered = await session.list_tools()

    # The current SDK client requests its latest supported version; the server
    # correctly negotiates it while retaining 2025-06-18 as the documented
    # compatibility baseline (covered by the explicit initialize test below).
    assert initialized.protocolVersion == mcp_types.LATEST_PROTOCOL_VERSION
    assert initialized.serverInfo.name == "clarity-legal"
    assert initialized.capabilities.tools is not None
    assert [tool.name for tool in discovered.tools] == ["search_caselaw"]
    assert discovered.tools[0].annotations is not None
    assert discovered.tools[0].annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_standard_initialize_returns_jsonrpc_success(monkeypatch, protocol_app):
    await _allow_identity(monkeypatch, "search_caselaw")
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            mcp_protocol.MCP_ENDPOINT_PATH,
            headers=_protocol_headers(),
            json={
                "jsonrpc": "2.0",
                "id": "init-1",
                "method": "initialize",
                "params": {
                    "protocolVersion": mcp_protocol.MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "compat-test", "version": "1.0"},
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == "init-1"
    assert body["result"]["protocolVersion"] == mcp_protocol.MCP_PROTOCOL_VERSION
    assert body["result"]["serverInfo"]["name"] == "clarity-legal"
    assert "tools" in body["result"]["capabilities"]
    assert "Mcp-Session-Id" not in response.headers


@pytest.mark.asyncio
async def test_initialized_notification_is_accepted(monkeypatch, protocol_app):
    await _allow_identity(monkeypatch, "search_caselaw")
    headers = {
        **_protocol_headers(),
        "Mcp-Protocol-Version": mcp_protocol.MCP_PROTOCOL_VERSION,
    }
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            mcp_protocol.MCP_ENDPOINT_PATH,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_tools_call_returns_canonical_structured_result(
    monkeypatch, protocol_app
):
    await _allow_identity(monkeypatch, "search_caselaw")

    async def execute_product_tool(*, name, arguments, request):
        assert name == "search_caselaw"
        assert arguments == {"query": "qualified immunity", "top_k": 1}
        assert request.headers["X-MCP-API-Key"] == "clmcp_test"
        return {
            "content": [
                {
                    "type": "json",
                    "json": {"results": [{"citation": "410 U.S. 113"}]},
                }
            ],
            "isError": False,
        }

    monkeypatch.setattr(mcp_protocol, "execute_product_tool", execute_product_tool)
    headers = {
        **_protocol_headers(),
        "Mcp-Protocol-Version": mcp_protocol.MCP_PROTOCOL_VERSION,
    }
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            mcp_protocol.MCP_ENDPOINT_PATH,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search_caselaw",
                    "arguments": {
                        "query": "qualified immunity",
                        "top_k": 1,
                    },
                },
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert result["structuredContent"] == {"results": [{"citation": "410 U.S. 113"}]}


@pytest.mark.asyncio
async def test_tools_call_enforces_product_scope(monkeypatch, protocol_app):
    await _allow_identity(monkeypatch, "get_chunk")

    async def should_not_execute(**kwargs):
        raise AssertionError("disallowed tool reached the execution gateway")

    monkeypatch.setattr(mcp_protocol, "execute_product_tool", should_not_execute)
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            mcp_protocol.MCP_ENDPOINT_PATH,
            headers={
                **_protocol_headers(),
                "Mcp-Protocol-Version": mcp_protocol.MCP_PROTOCOL_VERSION,
            },
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "search_caselaw",
                    "arguments": {"query": "tax"},
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    assert "not allowed" in response.json()["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_sdk_rejects_untrusted_host(monkeypatch, protocol_app):
    await _allow_identity(monkeypatch, "search_caselaw")
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://attacker.invalid",
    ) as client:
        response = await client.post(
            mcp_protocol.MCP_ENDPOINT_PATH,
            headers=_protocol_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": mcp_protocol.MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )

    assert response.status_code == 421
