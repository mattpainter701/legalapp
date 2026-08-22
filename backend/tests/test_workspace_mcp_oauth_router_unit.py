"""Unit coverage for the workspace MCP OAuth router (no database/network)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import workspace_mcp_oauth as oauth
from app.services.workspace_mcp_oauth import WORKSPACE_SCOPE_LABELS, WorkspaceOAuthError


class Result:
    def __init__(self, value=None):
        self.value = value

    def all(self):
        return self.value or []


class DB:
    def __init__(self, scalar=None, rows=None):
        self.scalar_value = scalar
        self.rows = rows or []
        self.added = []
        self.commit = AsyncMock(side_effect=self._commit)
        self.rollback = AsyncMock()

    async def scalar(self, _query):
        if isinstance(self.scalar_value, list):
            return self.scalar_value.pop(0) if self.scalar_value else None
        return self.scalar_value

    async def scalars(self, _query):
        return Result(self.rows)

    def add(self, value):
        self.added.append(value)

    async def _commit(self):
        for value in self.added:
            if getattr(value, "created_at", None) is None:
                value.created_at = datetime.now(timezone.utc)


def req(**form):
    return SimpleNamespace(
        state=SimpleNamespace(request_id="rid"),
        app=SimpleNamespace(state=SimpleNamespace(redis=None)),
        form=AsyncMock(return_value=form),
        json=AsyncMock(return_value=form),
    )


def client(client_id="client-1", redirects=None):
    redirects = redirects or ["https://app.example/callback"]
    return SimpleNamespace(
        client_id=client_id,
        client_name="Test client",
        redirect_uris=redirects,
        redirect_uri_set=set(redirects),
        grant_types=["authorization_code", "refresh_token"],
        is_active=lambda: True,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=2),
        last_used_at=None,
    )


def user():
    tenant = SimpleNamespace(name="Acme", is_active=True)
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        tenant=tenant,
        email="u@example.com",
        full_name="Test User",
        is_active=True,
        license_active=True,
        privacy_mode=False,
    )


def grant(u, c="client-1", status="active", expires=None):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=u.tenant_id,
        user_id=u.id,
        client_id=c,
        client_name="Test client",
        status=status,
        expires_at=expires or datetime.now(timezone.utc) + timedelta(days=1),
        created_at=datetime.now(timezone.utc),
        last_used_at=None,
        revoked_at=None,
        revoked_by_user_id=None,
        revocation_reason=None,
        scope_set=frozenset({"matters:read"}),
    )


@pytest.mark.asyncio
async def test_helpers_gate_redirect_and_scope_items(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        oauth._require_enabled()
    assert exc.value.status_code == 404
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", True)
    assert parse_qs(
        urlsplit(oauth._append_redirect_query("https://x/c?q=1", a="2", b=None)).query
    ) == {"q": ["1"], "a": ["2"]}
    assert oauth._scope_items(frozenset({"matters:read"}))[0]["label"]
    assert oauth._oauth_error(WorkspaceOAuthError("bad", "no")).status_code == 400


@pytest.mark.asyncio
async def test_document_read_scopes_require_document_management(monkeypatch):
    current_user = user()
    monkeypatch.setattr(
        oauth,
        "get_user_capabilities",
        AsyncMock(return_value=["manage_matters", "manage_documents"]),
    )
    allowed = await oauth._allowed_user_scopes(DB(), current_user)
    assert {"documents:read", "templates:read"}.issubset(allowed)

    monkeypatch.setattr(
        oauth,
        "get_user_capabilities",
        AsyncMock(return_value=["manage_matters"]),
    )
    restricted = await oauth._allowed_user_scopes(DB(), current_user)
    assert "documents:read" not in restricted
    assert "templates:read" not in restricted


@pytest.mark.asyncio
async def test_metadata_and_jwks(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", True)
    monkeypatch.setattr(
        oauth.settings, "WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED", True
    )
    metadata = await oauth.authorization_server_metadata()
    protected = await oauth.protected_resource_metadata()
    assert metadata["registration_endpoint"].endswith("/register")
    assert set(oauth._SCOPE_APP_CAPABILITIES) == set(WORKSPACE_SCOPE_LABELS)
    assert set(metadata["scopes_supported"]) == set(WORKSPACE_SCOPE_LABELS)
    assert protected["scopes_supported"] == metadata["scopes_supported"]
    assert {"documents:read", "templates:read"}.issubset(metadata["scopes_supported"])
    assert "authorization_servers" in protected
    assert "keys" in await oauth.workspace_jwks_endpoint()


@pytest.mark.asyncio
async def test_register_valid_and_invalid_metadata_rolls_back(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", True)
    monkeypatch.setattr(
        oauth.settings, "WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED", True
    )
    db = DB()
    response = await oauth.register_workspace_client(
        req(client_name="Desktop", redirect_uris=["https://x/c"]), db
    )
    assert response.status_code == 201 and db.added
    db = DB()
    response = await oauth.register_workspace_client(
        req(client_name="", redirect_uris=[]), db
    )
    assert response.status_code == 400 and db.rollback.await_count == 1
    db = DB()
    response = await oauth.register_workspace_client(
        req(client_name="D", redirect_uris=["https://x/c", "https://x/c"]), db
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_begin_authorization_success_and_error(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", True)
    legacy_resource = "https://lawhand.test/api/mcp/workspace"
    canonical_resource = "https://mcp.lawhand.test/api/mcp/workspace"
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_RESOURCE", legacy_resource)
    monkeypatch.setattr(
        oauth.settings, "WORKSPACE_MCP_CANONICAL_RESOURCE", canonical_resource
    )
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_RESOURCE_ALIASES", "")
    c = client()
    monkeypatch.setattr(oauth, "_active_client", AsyncMock(return_value=c))
    monkeypatch.setattr(oauth, "normalized_scopes", lambda s: frozenset(s.split()))
    monkeypatch.setattr(oauth, "validate_pkce_challenge", lambda *_: None)
    save = AsyncMock(return_value="req-1")
    monkeypatch.setattr(oauth, "save_authorization_request", save)
    out = await oauth.begin_workspace_authorization(
        req(),
        "code",
        "client-1",
        c.redirect_uris[0],
        "matters:read",
        "st",
        "a" * 43,
        "S256",
        legacy_resource,
        DB(),
    )
    assert out.status_code == 302 and "request_id=req-1" in out.headers["location"]
    assert save.await_args.args[1]["resource"] == canonical_resource
    out = await oauth.begin_workspace_authorization(
        req(),
        "token",
        "client-1",
        c.redirect_uris[0],
        "matters:read",
        "",
        "a" * 43,
        "S256",
        canonical_resource,
        DB(),
    )
    assert out.status_code == 400 and out.body


@pytest.mark.asyncio
async def test_get_consent_success_expired_and_permission_denied(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", True)
    u = user()
    c = client()
    monkeypatch.setattr(oauth, "require_workspace_tenant_allowed", lambda *_: None)
    monkeypatch.setattr(
        oauth, "load_authorization_request", AsyncMock(return_value=None)
    )
    with pytest.raises(HTTPException) as e:
        await oauth.get_workspace_authorization_request("x", req(), u, DB())
    assert e.value.status_code == 410
    pending = {"client_id": c.client_id, "scopes": ["matters:read"]}
    monkeypatch.setattr(
        oauth, "load_authorization_request", AsyncMock(return_value=pending)
    )
    monkeypatch.setattr(oauth, "_active_client", AsyncMock(return_value=c))
    monkeypatch.setattr(
        oauth, "_allowed_user_scopes", AsyncMock(return_value=frozenset())
    )
    with pytest.raises(HTTPException) as e:
        await oauth.get_workspace_authorization_request("x", req(), u, DB())
    assert e.value.status_code == 403
    monkeypatch.setattr(
        oauth,
        "_allowed_user_scopes",
        AsyncMock(return_value=frozenset({"matters:read"})),
    )
    assert (await oauth.get_workspace_authorization_request("x", req(), u, DB()))[
        "client"
    ]["id"] == c.client_id


@pytest.mark.asyncio
async def test_decide_deny_approve_and_rollback_restoration(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", True)
    u = user()
    c = client()
    pending = {
        "client_id": c.client_id,
        "redirect_uri": c.redirect_uris[0],
        "scopes": ["matters:read"],
        "state": "s",
        "code_challenge": "cc",
    }
    monkeypatch.setattr(oauth, "require_workspace_tenant_allowed", lambda *_: None)
    monkeypatch.setattr(
        oauth, "claim_authorization_request", AsyncMock(return_value=pending)
    )
    monkeypatch.setattr(oauth, "_active_client", AsyncMock(return_value=c))
    monkeypatch.setattr(
        oauth,
        "_allowed_user_scopes",
        AsyncMock(return_value=frozenset({"matters:read"})),
    )
    monkeypatch.setattr(oauth, "append_workspace_mcp_audit", AsyncMock())
    monkeypatch.setattr(oauth, "finalize_authorization_request", AsyncMock())
    denied = await oauth.decide_workspace_authorization(
        "r", oauth.ConsentDecision(approved=False), req(), u, DB()
    )
    assert "access_denied" in denied["redirect_to"]
    g = grant(u)
    db = DB()
    monkeypatch.setattr(oauth, "replace_active_grant", AsyncMock(return_value=g))
    monkeypatch.setattr(
        oauth, "save_authorization_code", AsyncMock(return_value="code")
    )
    approved = await oauth.decide_workspace_authorization(
        "r", oauth.ConsentDecision(approved=True), req(), u, db
    )
    assert "code=code" in approved["redirect_to"]
    monkeypatch.setattr(
        oauth, "replace_active_grant", AsyncMock(side_effect=RuntimeError("boom"))
    )
    restore = AsyncMock()
    monkeypatch.setattr(oauth, "restore_authorization_request", restore)
    with pytest.raises(RuntimeError):
        await oauth.decide_workspace_authorization(
            "r", oauth.ConsentDecision(approved=True), req(), u, DB()
        )
    assert restore.await_count == 1


@pytest.mark.asyncio
async def test_token_code_binding_and_success(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", True)
    c = client()
    u = user()
    g = grant(u)
    db = DB(c)
    monkeypatch.setattr(oauth, "load_authorization_code", AsyncMock(return_value=None))
    bad = await oauth.workspace_token(
        req(
            grant_type="authorization_code",
            client_id=c.client_id,
            resource=oauth.workspace_resource_uri(),
            code="x",
        ),
        db,
    )
    assert bad.status_code == 400 and db.rollback.await_count
    payload = {
        "client_id": c.client_id,
        "redirect_uri": c.redirect_uris[0],
        "resource": oauth.workspace_resource_uri(),
        "code_challenge": "cc",
        "tenant_id": str(u.tenant_id),
        "user_id": str(u.id),
        "grant_id": str(g.id),
        "scopes": ["matters:read"],
    }
    monkeypatch.setattr(
        oauth, "load_authorization_code", AsyncMock(return_value=payload)
    )
    monkeypatch.setattr(oauth, "verify_pkce", lambda *_: True)
    monkeypatch.setattr(oauth, "_load_grant_actor", AsyncMock(return_value=(g, u)))
    monkeypatch.setattr(
        oauth, "consume_authorization_code", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        oauth, "mint_workspace_access_token", lambda **_: ("at", "tid", 60)
    )
    monkeypatch.setattr(oauth, "issue_refresh_token", AsyncMock(return_value="rt"))
    monkeypatch.setattr(oauth, "append_workspace_mcp_audit", AsyncMock())
    out = await oauth.workspace_token(
        req(
            grant_type="authorization_code",
            client_id=c.client_id,
            resource=oauth.workspace_resource_uri(),
            code="x",
            redirect_uri=c.redirect_uris[0],
            code_verifier="v",
        ),
        db,
    )
    assert out.status_code == 200 and b'"access_token":"at"' in out.body


@pytest.mark.asyncio
async def test_token_refresh_success_replay_cleanup_and_revocation(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", True)
    c = client()
    u = user()
    g = grant(u)
    db = DB(c)
    payload = {
        "client_id": c.client_id,
        "resource": oauth.workspace_resource_uri(),
        "family_id": "fam",
        "tenant_id": str(u.tenant_id),
        "user_id": str(u.id),
        "grant_id": str(g.id),
        "scopes": ["matters:read"],
    }
    monkeypatch.setattr(
        oauth, "consume_refresh_token", AsyncMock(return_value=("consumed", payload))
    )
    monkeypatch.setattr(oauth, "_load_grant_actor", AsyncMock(return_value=(g, u)))
    monkeypatch.setattr(
        oauth, "mint_workspace_access_token", lambda **_: ("at", "tid", 60)
    )
    monkeypatch.setattr(oauth, "issue_refresh_token", AsyncMock(return_value="rt"))
    monkeypatch.setattr(oauth, "append_workspace_mcp_audit", AsyncMock())
    out = await oauth.workspace_token(
        req(
            grant_type="refresh_token",
            client_id=c.client_id,
            resource=oauth.workspace_resource_uri(),
            refresh_token="wmr_x",
        ),
        db,
    )
    assert out.status_code == 200
    revoke = AsyncMock()
    monkeypatch.setattr(oauth, "revoke_refresh_family", revoke)
    monkeypatch.setattr(
        oauth, "consume_refresh_token", AsyncMock(return_value=("replay", "fam"))
    )
    out = await oauth.workspace_token(
        req(
            grant_type="refresh_token",
            client_id=c.client_id,
            resource=oauth.workspace_resource_uri(),
            refresh_token="wmr_x",
        ),
        db,
    )
    assert out.status_code == 400 and revoke.await_count
    monkeypatch.setattr(
        oauth, "consume_refresh_token", AsyncMock(return_value=("none", None))
    )
    out = await oauth.workspace_token(
        req(
            grant_type="refresh_token",
            client_id=c.client_id,
            resource=oauth.workspace_resource_uri(),
            refresh_token="wmr_x",
        ),
        db,
    )
    assert out.status_code == 400


@pytest.mark.asyncio
async def test_revocation_grants_expiry_idempotence_and_audit(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ENABLED", True)
    u = user()
    g = grant(u, expires=datetime.now(timezone.utc) - timedelta(seconds=1))
    db = DB(rows=[g])
    monkeypatch.setattr(oauth, "require_workspace_tenant_allowed", lambda *_: None)
    out = await oauth.list_workspace_grants(u, db)
    assert out["items"][0]["status"] == "expired" and db.commit.await_count == 1
    g = grant(u)
    db = DB(g)
    monkeypatch.setattr(oauth, "append_workspace_mcp_audit", AsyncMock())
    out = await oauth.revoke_workspace_grant(
        g.id, oauth.RevokeGrantRequest(reason="bye"), req(), u, db
    )
    assert out["status"] == "revoked"
    out = await oauth.revoke_workspace_grant(
        g.id, oauth.RevokeGrantRequest(), req(), u, db
    )
    assert out["status"] == "revoked"
    event = SimpleNamespace(
        id=uuid4(),
        event_type="x",
        client_id="c",
        grant_id=g.id,
        tool_name=None,
        outcome="success",
        metadata_json={"a": 1},
        chain_position=1,
        event_hash="h",
        prev_event_hash=None,
        created_at=datetime.now(timezone.utc),
    )
    db = DB(rows=[event])
    audit = await oauth.list_workspace_audit(50, u, db)
    assert audit["items"][0]["metadata"] == {"a": 1}
