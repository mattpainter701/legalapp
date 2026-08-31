from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.routing import Route

from app.schemas.chat_action import ProposeClientSmsArgs
from app.services import workspace_mcp_protocol
from app.services.automation_capabilities import CapabilityContext
from app.services.chat_tools.handlers import _workspace_sms_idempotency_binding


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def running_workspace_protocol_manager():
    started = asyncio.Event()
    stop = asyncio.Event()

    async def run_manager():
        async with workspace_mcp_protocol.workspace_protocol_session_manager.run():
            started.set()
            await stop.wait()

    task = asyncio.create_task(run_manager())
    await started.wait()
    yield
    stop.set()
    await task


@pytest.fixture
def protocol_app(monkeypatch):
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "BACKEND_URL",
        "http://localhost:8000",
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.workspace_protocol_session_manager,
        "security_settings",
        workspace_mcp_protocol._transport_security(),
    )
    app = Starlette(
        routes=[
            Route(
                workspace_mcp_protocol.MCP_ENDPOINT_PATH,
                endpoint=workspace_mcp_protocol.workspace_protocol_endpoint,
                methods=["GET", "POST", "DELETE"],
            )
        ]
    )
    app.state.redis = None
    app.state.jti_blacklist = {}
    return app


def test_transport_security_allows_canonical_and_legacy_workspace_hosts(monkeypatch):
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_RESOURCE",
        "https://getlawhand.com/api/mcp/workspace",
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_CANONICAL_RESOURCE",
        "https://mcp.getlawhand.com/api/mcp/workspace",
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_RESOURCE_ALIASES",
        "",
    )

    security = workspace_mcp_protocol._transport_security()

    expected_hosts = {"mcp.getlawhand.com", "getlawhand.com"}
    expected_origins = {"https://mcp.getlawhand.com"}
    assert set(security.allowed_hosts) & expected_hosts == expected_hosts
    assert set(security.allowed_origins) & expected_origins == expected_origins


def test_bearer_challenge_uses_resource_origin_not_oauth_issuer(monkeypatch):
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_RESOURCE",
        "https://getlawhand.com/api/mcp/workspace",
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_CANONICAL_RESOURCE",
        "https://mcp.getlawhand.com/api/mcp/workspace",
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings, "WORKSPACE_MCP_RESOURCE_ALIASES", ""
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_ISSUER",
        "https://getlawhand.com",
    )

    challenge = workspace_mcp_protocol.workspace_bearer_challenge()

    assert (
        'resource_metadata="https://mcp.getlawhand.com'
        '/.well-known/oauth-protected-resource/api/mcp/workspace"'
    ) in challenge
    for scope in workspace_mcp_protocol.KNOWN_WORKSPACE_SCOPES:
        assert scope in challenge


def _identity(
    *, scopes: set[str], app_capabilities: set[str]
) -> workspace_mcp_protocol.WorkspaceMCPIdentity:
    return workspace_mcp_protocol.WorkspaceMCPIdentity(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        client_id="desktop-test-client",
        grant_id=str(uuid.uuid4()),
        token_id="token-test",
        scopes=frozenset(scopes),
        app_capabilities=frozenset(app_capabilities),
    )


async def _allow_identity(monkeypatch, identity):
    async def authenticate(scope):
        return identity

    monkeypatch.setattr(
        workspace_mcp_protocol, "authenticate_workspace_request", authenticate
    )
    monkeypatch.setattr(workspace_mcp_protocol.settings, "WORKSPACE_MCP_ENABLED", True)


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer workspace_test",
    }


def _initialize() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "init-1",
        "method": "initialize",
        "params": {
            "protocolVersion": workspace_mcp_protocol.MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "workspace-test", "version": "1"},
        },
    }


def test_workspace_sms_idempotency_binding_is_tenant_tool_global_and_canonical():
    tenant_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    party_id = uuid.uuid4()

    def context(*, actor_id: uuid.UUID, request_id: str) -> CapabilityContext:
        return CapabilityContext(
            db=None,
            user=SimpleNamespace(id=actor_id, tenant_id=tenant_id),
            channel="workspace_mcp",
            request_id=request_id,
            idempotency_key="stable-proposal-key",
        )

    base = ProposeClientSmsArgs(
        matter_id=matter_id,
        recipient_party_ids=[party_id],
        title="Review appointment SMS",
        body="First line\r\nSecond line",
        category="appointment",
    )
    normalized_transport = base.model_copy(update={"body": "First line\nSecond line"})
    first = _workspace_sms_idempotency_binding(
        context(actor_id=uuid.uuid4(), request_id="transport-1"), base
    )
    cross_actor_replay = _workspace_sms_idempotency_binding(
        context(actor_id=uuid.uuid4(), request_id="transport-2"),
        normalized_transport,
    )

    assert first == cross_actor_replay
    assert first is not None
    key_digest, request_digest, prefix, external_ref = first
    assert key_digest in prefix
    assert request_digest in external_ref
    assert "stable-proposal-key" not in external_ref

    changed_body = _workspace_sms_idempotency_binding(
        context(actor_id=uuid.uuid4(), request_id="transport-3"),
        base.model_copy(update={"body": "Changed customer-visible body"}),
    )
    changed_matter = _workspace_sms_idempotency_binding(
        context(actor_id=uuid.uuid4(), request_id="transport-4"),
        base.model_copy(update={"matter_id": uuid.uuid4()}),
    )
    assert changed_body is not None and changed_matter is not None
    assert changed_body[0] == key_digest == changed_matter[0]
    assert changed_body[1] != request_digest
    assert changed_matter[1] != request_digest


@pytest.mark.asyncio
async def test_workspace_endpoint_is_fail_closed_by_default(monkeypatch, protocol_app):
    monkeypatch.setattr(workspace_mcp_protocol.settings, "WORKSPACE_MCP_ENABLED", False)
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            workspace_mcp_protocol.MCP_ENDPOINT_PATH,
            headers=_headers(),
            json=_initialize(),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace MCP endpoint is disabled"}


@pytest.mark.asyncio
async def test_workspace_endpoint_requires_oauth_bearer(monkeypatch, protocol_app):
    monkeypatch.setattr(workspace_mcp_protocol.settings, "WORKSPACE_MCP_ENABLED", True)
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            workspace_mcp_protocol.MCP_ENDPOINT_PATH,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json=_initialize(),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Workspace OAuth bearer token required"}
    assert response.headers["www-authenticate"].startswith("Bearer ")
    assert "resource_metadata=" in response.headers["www-authenticate"]


def test_browser_session_jwt_cannot_be_replayed_as_workspace_token(monkeypatch):
    monkeypatch.setattr(
        workspace_mcp_protocol.settings, "WORKSPACE_MCP_ISSUER", "lawhand-oauth"
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_AUDIENCE",
        "lawhand-workspace-mcp",
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_TOKEN_SIGNING_KEY",
        "w" * 48,
    )
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "jti": "browser-session",
            "exp": int(time.time()) + 300,
        },
        workspace_mcp_protocol.settings.SECRET_KEY,
        algorithm=workspace_mcp_protocol.settings.ALGORITHM,
    )

    with pytest.raises(Exception) as exc:
        workspace_mcp_protocol.decode_workspace_access_token(token)

    assert getattr(exc.value, "status_code", None) == 401


def test_dedicated_workspace_token_binds_actor_client_grant_and_scopes(monkeypatch):
    issuer = "lawhand-oauth"
    audience = "lawhand-workspace-mcp"
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    signing_key = "w" * 48
    monkeypatch.setattr(workspace_mcp_protocol.settings, "WORKSPACE_MCP_ISSUER", issuer)
    monkeypatch.setattr(
        workspace_mcp_protocol.settings, "WORKSPACE_MCP_AUDIENCE", audience
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_TOKEN_SIGNING_KEY",
        signing_key,
    )
    token = jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "type": "workspace_mcp",
            "token_use": "access",
            "client_id": "claude-desktop",
            "grant_id": str(grant_id),
            "jti": "token-123",
            "scope": "matters:read tasks:read",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        signing_key,
        algorithm=workspace_mcp_protocol.settings.ALGORITHM,
    )

    identity = workspace_mcp_protocol.decode_workspace_access_token(token)

    assert identity.user_id == user_id
    assert identity.tenant_id == tenant_id
    assert identity.client_id == "claude-desktop"
    assert identity.grant_id == str(grant_id)
    assert identity.scopes == frozenset({"matters:read", "tasks:read"})


@pytest.mark.parametrize("missing_claim", ["iat", "exp"])
def test_workspace_token_requires_temporal_claims(monkeypatch, missing_claim):
    issuer = "lawhand-oauth"
    audience = "lawhand-workspace-mcp"
    signing_key = "w" * 48
    monkeypatch.setattr(workspace_mcp_protocol.settings, "WORKSPACE_MCP_ISSUER", issuer)
    monkeypatch.setattr(
        workspace_mcp_protocol.settings, "WORKSPACE_MCP_AUDIENCE", audience
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_TOKEN_SIGNING_KEY",
        signing_key,
    )
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "type": "workspace_mcp",
        "token_use": "access",
        "client_id": "desktop-client",
        "grant_id": str(uuid.uuid4()),
        "jti": "required-claim-token",
        "scope": "matters:read",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    claims.pop(missing_claim)
    token = jwt.encode(
        claims,
        signing_key,
        algorithm=workspace_mcp_protocol.settings.ALGORITHM,
    )

    with pytest.raises(Exception) as exc:
        workspace_mcp_protocol.decode_workspace_access_token(token)

    assert getattr(exc.value, "status_code", None) == 401


@pytest.mark.parametrize(
    ("issued_offset", "expires_offset"),
    [(300, 600), (0, 3_601), (0, 0)],
)
def test_workspace_token_rejects_invalid_or_unbounded_lifetime(
    monkeypatch, issued_offset, expires_offset
):
    issuer = "lawhand-oauth"
    audience = "lawhand-workspace-mcp"
    signing_key = "w" * 48
    now = int(time.time())
    monkeypatch.setattr(workspace_mcp_protocol.settings, "WORKSPACE_MCP_ISSUER", issuer)
    monkeypatch.setattr(
        workspace_mcp_protocol.settings, "WORKSPACE_MCP_AUDIENCE", audience
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_TOKEN_SIGNING_KEY",
        signing_key,
    )
    monkeypatch.setattr(
        workspace_mcp_protocol.settings,
        "WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES",
        60,
    )
    token = jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "type": "workspace_mcp",
            "token_use": "access",
            "client_id": "desktop-client",
            "grant_id": str(uuid.uuid4()),
            "jti": "bounded-lifetime-token",
            "scope": "matters:read",
            "iat": now + issued_offset,
            "exp": now + expires_offset,
        },
        signing_key,
        algorithm=workspace_mcp_protocol.settings.ALGORITHM,
    )

    with pytest.raises(Exception) as exc:
        workspace_mcp_protocol.decode_workspace_access_token(token)

    assert getattr(exc.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_official_client_discovers_only_scope_and_rbac_allowed_tools(
    monkeypatch, protocol_app
):
    identity = _identity(
        scopes={"matters:read", "tasks:read", "tasks:propose"},
        app_capabilities={"manage_matters"},
    )
    await _allow_identity(monkeypatch, identity)

    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
        headers={"Authorization": "Bearer workspace_test"},
    ) as http_client:
        async with streamable_http_client(
            "http://localhost:8000/api/mcp/workspace",
            http_client=http_client,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                discovered = await session.list_tools()

    names = {tool.name for tool in discovered.tools}
    assert initialized.serverInfo.name == "lawhand-workspace"
    assert names == {
        "find_matter",
        "get_matter_context",
        "list_matter_tasks",
        "propose_task",
        "search_matters",
        "search_tasks",
        "get_task",
    }
    proposed = next(tool for tool in discovered.tools if tool.name == "propose_task")
    assert proposed.annotations is not None
    assert proposed.annotations.readOnlyHint is False
    assert proposed.annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_workspace_call_returns_reviewable_proposal_as_structured_content(
    monkeypatch, protocol_app
):
    identity = _identity(
        scopes={"matters:read", "tasks:propose"},
        app_capabilities={"manage_matters"},
    )
    await _allow_identity(monkeypatch, identity)

    async def execute(*, name, arguments, request, identity):
        assert name == "propose_task"
        assert arguments["title"] == "Review next case steps"
        assert request.headers["Authorization"] == "Bearer workspace_test"
        return {
            "task_id": str(uuid.uuid4()),
            "status": "review",
            "approval_effect": "Approving moves this task into active work.",
        }

    monkeypatch.setattr(workspace_mcp_protocol, "execute_workspace_capability", execute)
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            workspace_mcp_protocol.MCP_ENDPOINT_PATH,
            headers={
                **_headers(),
                "Mcp-Protocol-Version": workspace_mcp_protocol.MCP_PROTOCOL_VERSION,
            },
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "propose_task",
                    "arguments": {
                        "matter_id": str(uuid.uuid4()),
                        "title": "Review next case steps",
                    },
                },
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["status"] == "review"


@pytest.mark.asyncio
async def test_workspace_sms_call_preserves_idempotency_identity_and_review_boundary(
    monkeypatch, protocol_app
):
    identity = _identity(
        scopes={"matters:read", "contacts:read", "communications:propose"},
        app_capabilities={"manage_matters"},
    )
    await _allow_identity(monkeypatch, identity)
    calls = []
    task_id = str(uuid.uuid4())

    async def execute(*, name, arguments, request, identity):
        assert name == "propose_client_sms"
        assert request.headers["X-Idempotency-Key"] == "mcp-sms-request-1"
        calls.append(
            (
                arguments,
                identity.user_id,
                request.headers["X-Request-ID"],
                workspace_mcp_protocol._workspace_idempotency_key(request),
            )
        )
        return {
            "task_id": task_id,
            "status": "review",
            "approval_effect": "Human approval is required before provider submission.",
            "pending_action": {
                "type": "sms_client",
                "recipient_bindings": [
                    {
                        "party_id": arguments["recipient_party_ids"][0],
                        "contact_id": str(uuid.uuid4()),
                        "phone": "+15551234567",
                    }
                ],
            },
        }

    monkeypatch.setattr(workspace_mcp_protocol, "execute_workspace_capability", execute)
    payload = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "propose_client_sms",
            "arguments": {
                "matter_id": str(uuid.uuid4()),
                "recipient_party_ids": [str(uuid.uuid4())],
                "title": "Review appointment SMS",
                "body": "Your appointment is tomorrow.",
                "category": "appointment",
            },
        },
    }
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        responses = []
        for request_id in (9, 10):
            response_payload = {**payload, "id": request_id}
            responses.append(
                await client.post(
                    workspace_mcp_protocol.MCP_ENDPOINT_PATH,
                    headers={
                        **_headers(),
                        "Mcp-Protocol-Version": workspace_mcp_protocol.MCP_PROTOCOL_VERSION,
                        "X-Idempotency-Key": "mcp-sms-request-1",
                        "X-Request-ID": f"transport-request-{request_id}",
                    },
                    json=response_payload,
                )
            )

    assert [response.status_code for response in responses] == [200, 200]
    assert [
        response.json()["result"]["structuredContent"]["task_id"]
        for response in responses
    ] == [task_id, task_id]
    assert all(
        response.json()["result"]["structuredContent"]["status"] == "review"
        for response in responses
    )
    assert len(calls) == 2
    assert [call[2] for call in calls] == [
        "transport-request-9",
        "transport-request-10",
    ]
    assert [call[3] for call in calls] == [
        "mcp-sms-request-1",
        "mcp-sms-request-1",
    ]


@pytest.mark.asyncio
async def test_workspace_call_rechecks_scope_before_dispatch(monkeypatch, protocol_app):
    identity = _identity(
        scopes={"matters:read"},
        app_capabilities={"manage_matters", "manage_documents"},
    )
    await _allow_identity(monkeypatch, identity)

    async def should_not_execute(**kwargs):
        raise AssertionError("scope-denied tool reached the capability handler")

    monkeypatch.setattr(
        workspace_mcp_protocol,
        "execute_workspace_capability",
        should_not_execute,
    )
    async with AsyncClient(
        transport=ASGITransport(app=protocol_app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.post(
            workspace_mcp_protocol.MCP_ENDPOINT_PATH,
            headers={
                **_headers(),
                "Mcp-Protocol-Version": workspace_mcp_protocol.MCP_PROTOCOL_VERSION,
            },
            json={
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "propose_matter_document",
                    "arguments": {
                        "matter_id": str(uuid.uuid4()),
                        "title": "Draft",
                        "body": "Body",
                    },
                },
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == ("capability_scope_denied")


def test_workspace_catalog_has_no_approval_send_or_execute_tools():
    names = {spec.name for spec in workspace_mcp_protocol._workspace_specs()}

    assert all(
        not name.startswith(("approve_", "send_", "execute_", "deliver_"))
        for name in names
    )
