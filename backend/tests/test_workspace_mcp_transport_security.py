from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Route

from app.services import workspace_mcp_protocol


def _identity() -> workspace_mcp_protocol.WorkspaceMCPIdentity:
    return workspace_mcp_protocol.WorkspaceMCPIdentity(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        client_id="transport-security-test",
        grant_id=str(uuid.uuid4()),
        token_id="transport-security-token",
        scopes=frozenset({"matter.read"}),
        app_capabilities=frozenset({"matter.read"}),
    )


def _app(*, methods: list[str]) -> Starlette:
    app = Starlette(
        routes=[
            Route(
                workspace_mcp_protocol.MCP_ENDPOINT_PATH,
                endpoint=workspace_mcp_protocol.workspace_protocol_endpoint,
                methods=methods,
            )
        ]
    )
    app.state.redis = None
    app.state.jti_blacklist = {}
    return app


@pytest.mark.asyncio
async def test_workspace_protocol_rejects_oversized_body_after_auth(monkeypatch):
    identity = _identity()

    async def authenticate(scope):
        return identity

    monkeypatch.setattr(
        workspace_mcp_protocol,
        "authenticate_workspace_request",
        authenticate,
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_ENABLED",
        True,
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "MCP_PROTOCOL_MAX_REQUEST_BYTES",
        32,
    )

    async with AsyncClient(
        transport=ASGITransport(app=_app(methods=["POST"])),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            workspace_mcp_protocol.MCP_ENDPOINT_PATH,
            headers={
                "Authorization": "Bearer workspace_test",
                "Content-Type": "application/json",
            },
            content=b"x" * 33,
        )

    assert response.status_code == 413
    assert response.json() == {
        "detail": "MCP request body exceeds the configured limit"
    }


@pytest.mark.asyncio
async def test_workspace_tool_rejects_non_object_arguments(monkeypatch):
    monkeypatch.setattr(
        workspace_mcp_protocol,
        "_request_and_identity",
        lambda: (object(), _identity()),
    )

    result = await workspace_mcp_protocol.call_workspace_tool(
        "matter.get",
        [],  # type: ignore[arg-type]
    )

    assert result.isError is True
    assert "Tool arguments must be an object" in result.content[0].text


@pytest.mark.asyncio
async def test_workspace_protocol_rejects_unexpected_methods(monkeypatch):
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_ENABLED",
        True,
    )
    async with AsyncClient(
        transport=ASGITransport(app=_app(methods=["PATCH"])),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.patch(workspace_mcp_protocol.MCP_ENDPOINT_PATH)

    assert response.status_code == 405
    assert response.headers["Allow"] == "GET, POST, DELETE"
