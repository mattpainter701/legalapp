from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import workspace_mcp_oauth as oauth_router


class _Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _DB:
    def __init__(self, *, scalar_values=None, rows=None):
        self.scalar_values = list(scalar_values or [])
        self.rows = list(rows or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def scalars(self, _statement):
        return _Rows(self.rows)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1
        for value in self.added:
            if getattr(value, "created_at", None) is None:
                value.created_at = datetime.now(timezone.utc)

    async def rollback(self):
        self.rollbacks += 1


class _Request:
    def __init__(self, *, json_data=None, form_data=None, redis=None):
        self._json_data = json_data
        self._form_data = form_data or {}
        self.app = SimpleNamespace(state=SimpleNamespace(redis=redis))
        self.state = SimpleNamespace(request_id="request-1")
        self.headers = {}

    async def json(self):
        return self._json_data

    async def form(self):
        return self._form_data


async def _value(value):
    return value


async def _none(*_args, **_kwargs):
    return None


def _user():
    tenant_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="attorney@example.test",
        full_name="Test Attorney",
        tenant=SimpleNamespace(id=tenant_id, name="Test Firm", is_active=True),
        is_active=True,
        license_active=True,
        privacy_mode=False,
    )


@pytest.mark.asyncio
async def test_registration_success_and_invalid_metadata(monkeypatch):
    monkeypatch.setattr(oauth_router.settings, "WORKSPACE_MCP_ENABLED", True)
    monkeypatch.setattr(
        oauth_router.settings, "WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED", True
    )
    monkeypatch.setattr(
        oauth_router.settings, "WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS", 30
    )
    payload = {
        "client_name": "Codex Desktop",
        "redirect_uris": ["http://127.0.0.1:43123/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "software_id": "codex",
        "software_version": "1.0",
    }
    db = _DB()
    response = await oauth_router.register_workspace_client(
        _Request(json_data=payload), db
    )
    content = json.loads(response.body)
    assert response.status_code == 201
    assert content["client_id"].startswith("lhmcp_")
    assert content["redirect_uris"] == payload["redirect_uris"]
    assert db.commits == 1 and len(db.added) == 1

    duplicate = {**payload, "redirect_uris": [payload["redirect_uris"][0]] * 2}
    invalid_db = _DB()
    invalid = await oauth_router.register_workspace_client(
        _Request(json_data=duplicate), invalid_db
    )
    assert invalid.status_code == 400
    assert json.loads(invalid.body)["error"] == "invalid_client_metadata"
    assert invalid_db.rollbacks == 1


@pytest.mark.asyncio
async def test_begin_and_get_authorization_request(monkeypatch):
    monkeypatch.setattr(oauth_router.settings, "WORKSPACE_MCP_ENABLED", True)
    monkeypatch.setattr(
        oauth_router, "require_workspace_tenant_allowed", lambda *_: None
    )
    monkeypatch.setattr(oauth_router.settings, "FRONTEND_URL", "https://app.test/")
    monkeypatch.setattr(
        oauth_router,
        "workspace_resource_uri",
        lambda: "https://lawhand.test/api/mcp/workspace",
    )
    client = SimpleNamespace(
        client_id="desktop-client",
        client_name="Desktop",
        redirect_uri_set=frozenset({"http://127.0.0.1:43123/callback"}),
    )
    monkeypatch.setattr(oauth_router, "_active_client", lambda *_args: _value(client))
    saved = []

    async def save_request(_request, payload):
        saved.append(payload)
        return "pending-request"

    monkeypatch.setattr(oauth_router, "save_authorization_request", save_request)
    response = await oauth_router.begin_workspace_authorization(
        _Request(),
        response_type="code",
        client_id=client.client_id,
        redirect_uri="http://127.0.0.1:43123/callback",
        scope="matters:read tasks:read",
        state="caller-state",
        code_challenge="a" * 43,
        code_challenge_method="S256",
        resource="https://lawhand.test/api/mcp/workspace",
        db=_DB(),
    )
    assert response.status_code == 302
    assert "request_id=pending-request" in response.headers["location"]
    assert saved[0]["scopes"] == ["matters:read", "tasks:read"]

    bad = await oauth_router.begin_workspace_authorization(
        _Request(),
        response_type="token",
        client_id=client.client_id,
        redirect_uri="http://127.0.0.1:43123/callback",
        scope="matters:read",
        state="",
        code_challenge="a" * 43,
        code_challenge_method="S256",
        resource="https://lawhand.test/api/mcp/workspace",
        db=_DB(),
    )
    assert bad.status_code == 400
    assert json.loads(bad.body)["error"] == "unsupported_response_type"

    user = _user()
    pending = {"client_id": client.client_id, "scopes": ["matters:read"]}
    monkeypatch.setattr(
        oauth_router, "load_authorization_request", lambda *_args: _value(pending)
    )
    monkeypatch.setattr(
        oauth_router,
        "_allowed_user_scopes",
        lambda *_args: _value(frozenset({"matters:read"})),
    )
    details = await oauth_router.get_workspace_authorization_request(
        "pending-request", _Request(), user, _DB()
    )
    assert details["organization"]["name"] == "Test Firm"
    assert details["client"]["name"] == "Desktop"

    monkeypatch.setattr(
        oauth_router, "load_authorization_request", lambda *_args: _value(None)
    )
    with pytest.raises(HTTPException) as expired:
        await oauth_router.get_workspace_authorization_request(
            "expired", _Request(), user, _DB()
        )
    assert expired.value.status_code == 410


@pytest.mark.asyncio
async def test_consent_denial_approval_and_restore_on_failure(monkeypatch):
    monkeypatch.setattr(oauth_router.settings, "WORKSPACE_MCP_ENABLED", True)
    monkeypatch.setattr(
        oauth_router, "require_workspace_tenant_allowed", lambda *_: None
    )
    monkeypatch.setattr(
        oauth_router,
        "workspace_resource_uri",
        lambda: "https://lawhand.test/api/mcp/workspace",
    )
    user = _user()
    redirect_uri = "http://127.0.0.1:43123/callback"
    pending = {
        "client_id": "desktop-client",
        "redirect_uri": redirect_uri,
        "scopes": ["matters:read"],
        "state": "caller-state",
        "code_challenge": "a" * 43,
    }
    client = SimpleNamespace(
        client_id="desktop-client",
        client_name="Desktop",
        redirect_uri_set=frozenset({redirect_uri}),
    )
    monkeypatch.setattr(
        oauth_router, "claim_authorization_request", lambda *_args: _value(pending)
    )
    monkeypatch.setattr(oauth_router, "_active_client", lambda *_args: _value(client))
    monkeypatch.setattr(
        oauth_router,
        "_allowed_user_scopes",
        lambda *_args: _value(frozenset({"matters:read"})),
    )
    monkeypatch.setattr(oauth_router, "append_workspace_mcp_audit", _none)
    finalized = []

    async def finalize(_request, request_id):
        finalized.append(request_id)

    monkeypatch.setattr(oauth_router, "finalize_authorization_request", finalize)
    denial_db = _DB()
    denied = await oauth_router.decide_workspace_authorization(
        "request-deny",
        oauth_router.ConsentDecision(approved=False),
        _Request(),
        user,
        denial_db,
    )
    assert "error=access_denied" in denied["redirect_to"]
    assert denial_db.commits == 1 and finalized == ["request-deny"]

    previous = SimpleNamespace(id=uuid.uuid4())
    grant = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(
        oauth_router, "replace_active_grant", lambda *_args, **_kwargs: _value(grant)
    )
    monkeypatch.setattr(
        oauth_router, "save_authorization_code", lambda *_args: _value("one-use-code")
    )
    revoked = []

    async def revoke_old(_request, grant_id):
        revoked.append(grant_id)

    monkeypatch.setattr(oauth_router, "revoke_grant_refresh_tokens", revoke_old)
    approval_db = _DB(scalar_values=[previous])
    approved = await oauth_router.decide_workspace_authorization(
        "request-approve",
        oauth_router.ConsentDecision(approved=True),
        _Request(),
        user,
        approval_db,
    )
    assert "code=one-use-code" in approved["redirect_to"]
    assert approval_db.commits == 1
    assert revoked == [previous.id]

    restored = []
    bad_client = SimpleNamespace(
        client_id="desktop-client", client_name="Desktop", redirect_uri_set=frozenset()
    )
    monkeypatch.setattr(
        oauth_router, "_active_client", lambda *_args: _value(bad_client)
    )
    monkeypatch.setattr(
        oauth_router,
        "restore_authorization_request",
        lambda _request, request_id: _record(restored, request_id),
    )
    failed_db = _DB()
    with pytest.raises(HTTPException) as mismatch:
        await oauth_router.decide_workspace_authorization(
            "request-bad",
            oauth_router.ConsentDecision(approved=True),
            _Request(),
            user,
            failed_db,
        )
    assert mismatch.value.status_code == 400
    assert failed_db.rollbacks == 1 and restored == ["request-bad"]


async def _record(items, value):
    items.append(value)


@pytest.mark.asyncio
async def test_token_authorization_code_refresh_and_replay(monkeypatch):
    monkeypatch.setattr(oauth_router.settings, "WORKSPACE_MCP_ENABLED", True)
    resource = "https://lawhand.test/api/mcp/workspace"
    monkeypatch.setattr(oauth_router, "workspace_resource_uri", lambda: resource)
    client = SimpleNamespace(client_id="desktop-client", last_used_at=None)
    user = _user()
    grant = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(oauth_router, "_active_client", lambda *_args: _value(client))
    monkeypatch.setattr(
        oauth_router,
        "_load_grant_actor",
        lambda *_args, **_kwargs: _value((grant, user)),
    )
    payload = {
        "tenant_id": str(user.tenant_id),
        "user_id": str(user.id),
        "grant_id": str(grant.id),
        "client_id": client.client_id,
        "redirect_uri": "http://127.0.0.1:43123/callback",
        "scopes": ["matters:read"],
        "code_challenge": "challenge",
        "resource": resource,
    }
    monkeypatch.setattr(
        oauth_router, "load_authorization_code", lambda *_args: _value(payload)
    )
    monkeypatch.setattr(oauth_router, "verify_pkce", lambda *_args: True)
    monkeypatch.setattr(
        oauth_router, "consume_authorization_code", lambda *_args: _value(True)
    )
    monkeypatch.setattr(
        oauth_router,
        "mint_workspace_access_token",
        lambda **_kwargs: ("access", "jti", 900),
    )
    monkeypatch.setattr(
        oauth_router, "issue_refresh_token", lambda *_args, **_kwargs: _value("refresh")
    )
    monkeypatch.setattr(oauth_router, "append_workspace_mcp_audit", _none)
    code_form = {
        "grant_type": "authorization_code",
        "client_id": client.client_id,
        "resource": resource,
        "code": "code",
        "redirect_uri": payload["redirect_uri"],
        "code_verifier": "verifier",
    }
    code_db = _DB()
    code_response = await oauth_router.workspace_token(
        _Request(form_data=code_form), code_db
    )
    assert json.loads(code_response.body)["access_token"] == "access"
    assert code_db.commits == 1 and client.last_used_at is not None

    consumed = {
        "family_id": "family-1",
        "client_id": client.client_id,
        "resource": resource,
        "tenant_id": str(user.tenant_id),
        "user_id": str(user.id),
        "grant_id": str(grant.id),
        "scopes": ["matters:read"],
    }
    monkeypatch.setattr(
        oauth_router,
        "consume_refresh_token",
        lambda *_args: _value(("consumed", consumed)),
    )
    refresh_response = await oauth_router.workspace_token(
        _Request(
            form_data={
                "grant_type": "refresh_token",
                "client_id": client.client_id,
                "resource": resource,
                "refresh_token": "wmr_old",
            }
        ),
        _DB(),
    )
    assert json.loads(refresh_response.body)["refresh_token"] == "refresh"

    revoked = []
    monkeypatch.setattr(
        oauth_router,
        "consume_refresh_token",
        lambda *_args: _value(("replay", "family-replay")),
    )
    monkeypatch.setattr(
        oauth_router,
        "revoke_refresh_family",
        lambda _request, family: _record(revoked, family),
    )
    replay = await oauth_router.workspace_token(
        _Request(
            form_data={
                "grant_type": "refresh_token",
                "client_id": client.client_id,
                "resource": resource,
                "refresh_token": "wmr_reused",
            }
        ),
        _DB(),
    )
    assert replay.status_code == 400
    assert json.loads(replay.body)["error"] == "invalid_grant"
    assert revoked == ["family-replay"]


class _Redis:
    def __init__(self):
        self.values = {}

    async def setex(self, key, ttl, value):
        self.values[key] = (ttl, value)


@pytest.mark.asyncio
async def test_revocation_grant_listing_and_audit_serialization(monkeypatch):
    monkeypatch.setattr(oauth_router.settings, "WORKSPACE_MCP_ENABLED", True)
    monkeypatch.setattr(
        oauth_router, "require_workspace_tenant_allowed", lambda *_: None
    )
    user = _user()
    now = datetime.now(timezone.utc)
    expired = SimpleNamespace(
        id=uuid.uuid4(),
        client_id="desktop-client",
        client_name="Desktop",
        scope_set=frozenset({"matters:read"}),
        status="active",
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(seconds=1),
        last_used_at=None,
        revoked_at=None,
    )
    grants_db = _DB(rows=[expired])
    listed = await oauth_router.list_workspace_grants(user, grants_db)
    assert listed["items"][0]["status"] == "expired"
    assert grants_db.commits == 1

    redis = _Redis()
    active = SimpleNamespace(
        id=uuid.uuid4(),
        client_id="desktop-client",
        status="active",
        revoked_at=None,
        revoked_by_user_id=None,
        revocation_reason=None,
    )
    monkeypatch.setattr(oauth_router, "append_workspace_mcp_audit", _none)
    refreshed = []
    monkeypatch.setattr(
        oauth_router,
        "revoke_grant_refresh_tokens",
        lambda _request, grant_id: _record(refreshed, grant_id),
    )
    revoke_db = _DB(scalar_values=[active])
    revoked = await oauth_router.revoke_workspace_grant(
        active.id,
        oauth_router.RevokeGrantRequest(reason="  Finished  "),
        _Request(redis=redis),
        user,
        revoke_db,
    )
    assert revoked == {"id": str(active.id), "status": "revoked"}
    assert active.revocation_reason == "Finished"
    assert revoke_db.commits == 1 and refreshed == [active.id]
    assert f"workspace_mcp_grant:{active.id}" in redis.values

    event = SimpleNamespace(
        id=uuid.uuid4(),
        event_type="tool_called",
        client_id="desktop-client",
        grant_id=active.id,
        tool_name="find_matter",
        outcome="success",
        metadata_json={"effect": "read"},
        chain_position=3,
        event_hash="a" * 64,
        prev_event_hash="b" * 64,
        created_at=now,
    )
    audit = await oauth_router.list_workspace_audit(25, user, _DB(rows=[event]))
    assert audit["items"][0]["event_hash"] == "a" * 64
    assert audit["items"][0]["previous_event_hash"] == "b" * 64


@pytest.mark.asyncio
async def test_rfc7009_revokes_refresh_replay_and_matching_access_token(monkeypatch):
    from app.services import workspace_mcp_protocol as protocol

    monkeypatch.setattr(oauth_router.settings, "WORKSPACE_MCP_ENABLED", True)
    client = SimpleNamespace(client_id="desktop-client")
    monkeypatch.setattr(oauth_router, "_active_client", lambda *_args: _value(client))

    unavailable = await oauth_router.revoke_workspace_token(
        _Request(form_data={"token": "wmr_missing", "client_id": client.client_id}),
        _DB(),
    )
    assert unavailable.status_code == 503

    redis = _Redis()
    revoked_families = []
    consumed = {"client_id": client.client_id, "family_id": "family-consumed"}
    monkeypatch.setattr(
        oauth_router,
        "consume_refresh_token",
        lambda *_args: _value(("consumed", consumed)),
    )
    monkeypatch.setattr(
        oauth_router,
        "revoke_refresh_family",
        lambda _request, family: _record(revoked_families, family),
    )
    refresh_result = await oauth_router.revoke_workspace_token(
        _Request(
            form_data={"token": "wmr_refresh", "client_id": client.client_id},
            redis=redis,
        ),
        _DB(),
    )
    assert refresh_result.status_code == 200
    assert revoked_families == ["family-consumed"]

    monkeypatch.setattr(
        oauth_router,
        "consume_refresh_token",
        lambda *_args: _value(("replay", "family-replayed")),
    )
    await oauth_router.revoke_workspace_token(
        _Request(
            form_data={"token": "wmr_replay", "client_id": client.client_id},
            redis=redis,
        ),
        _DB(),
    )
    assert revoked_families == ["family-consumed", "family-replayed"]

    identity = SimpleNamespace(client_id=client.client_id, token_id="access-token-jti")
    monkeypatch.setattr(
        protocol, "decode_workspace_access_token", lambda _token: identity
    )
    access_result = await oauth_router.revoke_workspace_token(
        _Request(
            form_data={"token": "access-token", "client_id": client.client_id},
            redis=redis,
        ),
        _DB(),
    )
    assert access_result.status_code == 200
    assert "jti:access-token-jti" in redis.values
