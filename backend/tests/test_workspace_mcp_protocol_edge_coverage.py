from __future__ import annotations

import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services import workspace_mcp_protocol as protocol
from app.services.automation_capabilities import CapabilityError
from app.services.workspace_mcp_grants import WorkspaceMCPGrantError


def _identity(**overrides):
    values = {
        "user_id": uuid4(),
        "tenant_id": uuid4(),
        "client_id": "desktop-client",
        "grant_id": str(uuid4()),
        "token_id": "token-id",
        "scopes": frozenset({"matters:read"}),
        "app_capabilities": frozenset({"manage_matters"}),
    }
    values.update(overrides)
    return protocol.WorkspaceMCPIdentity(**values)


class _DB:
    def __init__(self, scalar_value=None):
        self.scalar_value = scalar_value
        self.commits = 0
        self.rollbacks = 0

    async def scalar(self, _statement):
        return self.scalar_value

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


async def _value(value):
    return value


async def _none(*_args, **_kwargs):
    return None


def test_origin_parser_handles_invalid_ipv6_authority():
    assert protocol._origin_and_host("http://[broken") == (None, None)


@pytest.mark.asyncio
async def test_actor_loading_maps_grant_user_license_and_privacy_failures(monkeypatch):
    identity = _identity()
    monkeypatch.setattr(protocol, "set_tenant_context", _none)
    monkeypatch.setattr(protocol, "require_active_tenant", lambda _tenant: None)
    monkeypatch.setattr(
        protocol, "get_user_capabilities", lambda *_args: _value({"manage_matters"})
    )

    async def rejected_grant(*_args, **_kwargs):
        raise WorkspaceMCPGrantError("revoked")

    monkeypatch.setattr(protocol, "require_active_workspace_grant", rejected_grant)
    with pytest.raises(HTTPException) as grant_error:
        await protocol._load_workspace_actor(_DB(), identity)
    assert grant_error.value.status_code == 401
    assert "active consent grant" in grant_error.value.detail

    monkeypatch.setattr(protocol, "require_active_workspace_grant", _none)
    with pytest.raises(HTTPException, match="user is unavailable"):
        await protocol._load_workspace_actor(_DB(None), identity)

    for user, detail in [
        (
            SimpleNamespace(
                is_active=False,
                license_active=True,
                privacy_mode=False,
                tenant=object(),
                id=identity.user_id,
            ),
            "user is inactive",
        ),
        (
            SimpleNamespace(
                is_active=True,
                license_active=False,
                privacy_mode=False,
                tenant=object(),
                id=identity.user_id,
            ),
            "Standard license required",
        ),
        (
            SimpleNamespace(
                is_active=True,
                license_active=True,
                privacy_mode=True,
                tenant=object(),
                id=identity.user_id,
            ),
            "Privacy Mode",
        ),
    ]:
        with pytest.raises(HTTPException, match=detail):
            await protocol._load_workspace_actor(_DB(user), identity)

    user = SimpleNamespace(
        is_active=True,
        license_active=True,
        privacy_mode=False,
        tenant=object(),
        id=identity.user_id,
    )
    loaded_user, capabilities = await protocol._load_workspace_actor(
        _DB(user), identity
    )
    assert loaded_user is user
    assert capabilities == frozenset({"manage_matters"})


@pytest.mark.asyncio
async def test_capability_dispatch_rolls_back_reads_denials_and_missing_handlers(
    monkeypatch,
):
    from app.services.chat_tools import handlers

    request = Request({"type": "http", "headers": [(b"x-idempotency-key", b"idem")]})
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())

    async def read_handler(context, parsed):
        assert context.request_id == "idem"
        return {"query": parsed.query}

    db = _DB()
    monkeypatch.setattr(protocol, "async_session_maker", lambda: _Session(db))
    monkeypatch.setattr(
        protocol,
        "_load_workspace_actor",
        lambda *_args: _value((user, frozenset({"manage_matters"}))),
    )
    monkeypatch.setattr(handlers, "find_matter", read_handler)
    result = await protocol.execute_workspace_capability(
        name="find_matter",
        arguments={"query": "Smith"},
        request=request,
        identity=_identity(),
    )
    assert result == {"query": "Smith"}
    assert db.rollbacks == 1 and db.commits == 0

    denied_db = _DB()
    monkeypatch.setattr(protocol, "async_session_maker", lambda: _Session(denied_db))
    monkeypatch.setattr(
        protocol,
        "_load_workspace_actor",
        lambda *_args: _value((user, frozenset())),
    )
    with pytest.raises(CapabilityError) as denied:
        await protocol.execute_workspace_capability(
            name="find_matter",
            arguments={"query": "Smith"},
            request=request,
            identity=_identity(),
        )
    assert denied.value.code == "capability_scope_denied"
    assert denied_db.rollbacks == 1

    missing_db = _DB()
    monkeypatch.setattr(protocol, "async_session_maker", lambda: _Session(missing_db))
    monkeypatch.setattr(
        protocol,
        "_load_workspace_actor",
        lambda *_args: _value((user, frozenset({"manage_matters"}))),
    )
    monkeypatch.setattr(handlers, "find_matter", None)
    with pytest.raises(CapabilityError) as missing:
        await protocol.execute_workspace_capability(
            name="find_matter",
            arguments={"query": "Smith"},
            request=request,
            identity=_identity(),
        )
    assert missing.value.code == "unsupported_tool"
    assert missing_db.rollbacks == 1


@pytest.mark.asyncio
async def test_call_tool_maps_http_and_internal_failures(monkeypatch):
    request = Request({"type": "http", "headers": []})
    monkeypatch.setattr(
        protocol, "_request_and_identity", lambda: (request, _identity())
    )

    async def denied(**_kwargs):
        raise HTTPException(status_code=403, detail="license unavailable")

    monkeypatch.setattr(protocol, "execute_workspace_capability", denied)
    denied_result = await protocol.call_workspace_tool("find_matter", {"query": "x"})
    assert denied_result.structuredContent["error"]["code"] == "workspace_access_denied"

    async def crashed(**_kwargs):
        raise RuntimeError("database internals")

    monkeypatch.setattr(protocol, "execute_workspace_capability", crashed)
    failed = await protocol.call_workspace_tool("find_matter", {"query": "x"})
    assert failed.structuredContent["error"] == {
        "code": "internal_error",
        "message": "The workspace capability could not be completed",
    }


def _valid_claims():
    now = int(time.time())
    return {
        "type": "workspace_mcp",
        "token_use": "access",
        "client_id": "desktop-client",
        "grant_id": str(uuid4()),
        "jti": "token-id",
        "sub": str(uuid4()),
        "tenant_id": str(uuid4()),
        "scope": "matters:read",
        "iat": now,
        "exp": now + 120,
    }


def test_token_decoder_covers_configuration_claim_and_lifetime_validation(monkeypatch):
    monkeypatch.setattr(protocol.settings, "WORKSPACE_MCP_ISSUER", "")
    with pytest.raises(HTTPException) as unconfigured:
        protocol.decode_workspace_access_token("token")
    assert unconfigured.value.status_code == 503

    monkeypatch.setattr(protocol.settings, "WORKSPACE_MCP_ISSUER", "issuer")
    monkeypatch.setattr(
        protocol.jwt, "decode", lambda *_args, **_kwargs: _valid_claims()
    )
    decoded = protocol.decode_workspace_access_token("token")
    assert decoded.client_id == "desktop-client"

    for mutate in [
        lambda claims: claims.update(client_id=""),
        lambda claims: claims.update(jti="x" * 201),
        lambda claims: claims.update(iat="not-an-integer"),
        lambda claims: claims.update(iat=int(time.time()) + 120),
        lambda claims: claims.update(sub="not-a-uuid"),
    ]:
        claims = _valid_claims()
        mutate(claims)
        monkeypatch.setattr(
            protocol.jwt, "decode", lambda *_args, _claims=claims, **_kwargs: _claims
        )
        with pytest.raises(HTTPException, match="Invalid workspace access token"):
            protocol.decode_workspace_access_token("token")


@pytest.mark.asyncio
async def test_revocation_uses_redis_and_dev_blacklist_but_fails_closed_in_prod(
    monkeypatch,
):
    identity = _identity()

    class Redis:
        async def exists(self, *keys):
            assert keys == (
                f"jti:{identity.token_id}",
                f"workspace_mcp_grant:{identity.grant_id}",
            )
            return 1

    app = SimpleNamespace(state=SimpleNamespace(redis=Redis()))
    request = Request({"type": "http", "headers": [], "app": app})
    assert await protocol._workspace_token_is_revoked(request, identity)

    app.state.redis = None
    monkeypatch.setattr(protocol.settings, "DEV_MODE", False)
    with pytest.raises(HTTPException) as unavailable:
        await protocol._workspace_token_is_revoked(request, identity)
    assert unavailable.value.status_code == 503

    monkeypatch.setattr(protocol.settings, "DEV_MODE", True)
    app.state.jti_blacklist = {identity.token_id: time.time() + 60}
    assert await protocol._workspace_token_is_revoked(request, identity)
    app.state.jti_blacklist = {identity.token_id: time.time() - 1}
    assert not await protocol._workspace_token_is_revoked(request, identity)


@pytest.mark.asyncio
async def test_request_authentication_rejects_missing_and_revoked_bearers_then_commits(
    monkeypatch,
):
    app = SimpleNamespace(state=SimpleNamespace(redis=None, jti_blacklist={}))
    base_scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/mcp/workspace",
        "headers": [],
        "app": app,
    }
    with pytest.raises(HTTPException, match="bearer token required"):
        await protocol.authenticate_workspace_request(base_scope)

    identity = _identity()
    scope = {
        **base_scope,
        "headers": [(b"authorization", b"Bearer token")],
    }
    monkeypatch.setattr(
        protocol, "decode_workspace_access_token", lambda _token: identity
    )
    monkeypatch.setattr(
        protocol, "_workspace_token_is_revoked", lambda *_args: _value(True)
    )
    with pytest.raises(HTTPException, match="has been revoked"):
        await protocol.authenticate_workspace_request(scope)

    db = _DB()
    monkeypatch.setattr(
        protocol, "_workspace_token_is_revoked", lambda *_args: _value(False)
    )
    monkeypatch.setattr(protocol, "async_session_maker", lambda: _Session(db))
    monkeypatch.setattr(
        protocol,
        "_load_workspace_actor",
        lambda *_args: _value((object(), frozenset({"manage_matters"}))),
    )
    authenticated = await protocol.authenticate_workspace_request(scope)
    assert authenticated.app_capabilities == frozenset({"manage_matters"})
    assert db.commits == 1 and db.rollbacks == 0


@pytest.mark.asyncio
async def test_endpoint_maps_unexpected_authentication_failure_to_503(monkeypatch):
    monkeypatch.setattr(protocol.settings, "WORKSPACE_MCP_ENABLED", True)

    async def failed_auth(_scope):
        raise RuntimeError("credential backend details")

    monkeypatch.setattr(protocol, "authenticate_workspace_request", failed_auth)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/mcp/workspace",
        "headers": [],
    }
    await protocol.WorkspaceMCPProtocolEndpoint()(scope, receive, send)
    assert sent[0]["status"] == 503
    assert b"authentication is unavailable" in sent[1]["body"]


@pytest.mark.asyncio
async def test_enabled_protocol_lifespan_runs_session_manager(monkeypatch):
    entered = []

    @asynccontextmanager
    async def running():
        entered.append(True)
        yield

    monkeypatch.setattr(protocol.settings, "WORKSPACE_MCP_ENABLED", True)
    monkeypatch.setattr(protocol.workspace_protocol_session_manager, "run", running)

    async with protocol.workspace_protocol_lifespan():
        assert entered == [True]
