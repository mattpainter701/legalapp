from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.main import app
from app.routers import research_mcp_oauth as router
from app.routers import workspace_mcp_oauth as workspace_router
from app.services import research_mcp_oauth as oauth
from app.services import workspace_mcp_oauth as shared
from app.middleware.tenant import _is_license_exempt
from app.services import mcp_protocol
from app.services import mcp_product
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def research_oauth_settings(monkeypatch):
    monkeypatch.setattr(
        oauth.settings,
        "RESEARCH_MCP_PUBLIC_URL",
        "https://research.getlawhand.com/api/mcp",
    )
    monkeypatch.setattr(
        oauth.settings, "RESEARCH_MCP_ISSUER", "https://research.getlawhand.com"
    )
    monkeypatch.setattr(oauth.settings, "RESEARCH_MCP_AUDIENCE", "lawhand-research-mcp")
    monkeypatch.setattr(oauth.settings, "RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES", 15)
    monkeypatch.setattr(shared.settings, "WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64", "")
    monkeypatch.setattr(shared.settings, "WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64", "")
    monkeypatch.setattr(shared.settings, "WORKSPACE_MCP_TOKEN_SIGNING_KEY", "r" * 48)


def test_research_access_token_is_resource_audience_and_type_bound():
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    token, token_id, expires_in = oauth.mint_research_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        client_id="research.client",
        grant_id=grant_id,
        scopes=frozenset({oauth.RESEARCH_SCOPE}),
    )

    claims = oauth.decode_research_access_token(token)

    assert claims["sub"] == str(user_id)
    assert claims["tenant_id"] == str(tenant_id)
    assert claims["grant_id"] == str(grant_id)
    assert claims["jti"] == token_id
    assert claims["resource"] == "https://research.getlawhand.com/api/mcp"
    assert claims["type"] == "research_mcp"
    assert expires_in == 900


@pytest.mark.parametrize(
    "claim,value",
    [
        ("type", "workspace_mcp"),
        ("resource", "https://mcp.getlawhand.com/api/mcp/workspace"),
    ],
)
def test_research_access_token_rejects_cross_product_binding(claim, value):
    now = 2_000_000_000
    claims = {
        "iss": "https://research.getlawhand.com",
        "aud": "lawhand-research-mcp",
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "client_id": "research.client",
        "grant_id": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "scope": "research:read",
        "resource": "https://research.getlawhand.com/api/mcp",
        "type": "research_mcp",
        "token_use": "access",
        "iat": now - 10,
        "exp": now + 3600,
    }
    claims[claim] = value
    token = jwt.encode(claims, "r" * 48, algorithm="HS256")

    with pytest.raises(shared.WorkspaceOAuthError, match="binding"):
        oauth.decode_research_access_token(token)


@pytest.mark.parametrize("missing", ["exp", "iat", "jti", "sub", "aud", "iss"])
def test_research_access_token_requires_temporal_and_identity_claims(missing):
    now = int(__import__("time").time())
    claims = {
        "iss": "https://research.getlawhand.com",
        "aud": "lawhand-research-mcp",
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "client_id": "research.client",
        "grant_id": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "scope": "research:read",
        "resource": "https://research.getlawhand.com/api/mcp",
        "type": "research_mcp",
        "token_use": "access",
        "iat": now - 10,
        "exp": now + 600,
    }
    claims.pop(missing)
    token = jwt.encode(claims, "r" * 48, algorithm="HS256")
    with pytest.raises(shared.WorkspaceOAuthError, match="invalid"):
        oauth.decode_research_access_token(token)


@pytest.mark.parametrize(
    "iat_offset,exp_offset",
    [
        (120, 600),
        (-1, -1),
        (-1, 901),
    ],
)
def test_research_access_token_rejects_invalid_lifetime(iat_offset, exp_offset):
    # Compute timestamps at test execution so a slow suite cannot turn the
    # future-iat case into an otherwise-valid token before this assertion runs.
    now = int(time.time())
    iat = now + iat_offset
    exp = now + exp_offset
    claims = {
        "iss": "https://research.getlawhand.com",
        "aud": "lawhand-research-mcp",
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "client_id": "research.client",
        "grant_id": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "scope": "research:read",
        "resource": "https://research.getlawhand.com/api/mcp",
        "type": "research_mcp",
        "token_use": "access",
        "iat": iat,
        "exp": exp,
    }
    token = jwt.encode(claims, "r" * 48, algorithm="HS256")
    with pytest.raises(shared.WorkspaceOAuthError, match="invalid|lifetime"):
        oauth.decode_research_access_token(token)


@pytest.mark.parametrize("scope", ["", "matters:read", "research:read matters:read"])
def test_research_scope_is_exact(scope):
    with pytest.raises(shared.WorkspaceOAuthError):
        oauth.normalized_research_scopes(scope)


def test_research_scope_accepts_offline_access_for_refresh_tokens():
    assert oauth.normalized_research_scopes(
        "research:read offline_access"
    ) == frozenset({"research:read", "offline_access"})


def test_research_metadata_advertises_dcr_pkce_and_form_endpoints(monkeypatch):
    monkeypatch.setattr(
        router.settings, "RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED", True
    )
    metadata = router.authorization_server_metadata_payload()
    protected = router.protected_resource_metadata_payload()

    assert metadata["issuer"] == "https://research.getlawhand.com"
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["registration_endpoint"].endswith(
        "/api/research-mcp/oauth/register"
    )
    assert protected == {
        "resource": "https://research.getlawhand.com/api/mcp",
        "resource_name": "LawHand Research MCP",
        "authorization_servers": ["https://research.getlawhand.com"],
        "scopes_supported": ["offline_access", "research:read"],
        "bearer_methods_supported": ["header"],
    }


@pytest.mark.asyncio
async def test_shared_root_discovery_dispatches_by_dedicated_research_host(monkeypatch):
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(router.settings, "RESEARCH_MCP_OAUTH_ENABLED", True)
    request = SimpleNamespace(url=SimpleNamespace(hostname="research.getlawhand.com"))

    protected = await workspace_router.protected_resource_metadata(request)
    metadata = await workspace_router.authorization_server_metadata(request)

    assert protected["resource"] == "https://research.getlawhand.com/api/mcp"
    assert metadata["issuer"] == "https://research.getlawhand.com"


@pytest.mark.asyncio
async def test_research_asgi_discovery_and_bearer_challenge(monkeypatch):
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(router.settings, "RESEARCH_MCP_OAUTH_ENABLED", True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="https://research.getlawhand.com",
    ) as client:
        protected = await client.get("/.well-known/oauth-protected-resource/api/mcp")
        metadata = await client.get("/.well-known/oauth-authorization-server")
        unauthenticated = await client.post(
            "/api/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )

    assert protected.status_code == 200
    assert protected.json()["resource"] == "https://research.getlawhand.com/api/mcp"
    assert metadata.status_code == 200
    assert metadata.json()["issuer"] == "https://research.getlawhand.com"
    assert unauthenticated.status_code == 401
    assert (
        "oauth-protected-resource/api/mcp"
        in unauthenticated.headers["www-authenticate"]
    )


@pytest.mark.asyncio
async def test_research_asgi_rejects_apex_mcp_and_oauth_routes(monkeypatch):
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(router.settings, "RESEARCH_MCP_OAUTH_ENABLED", True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="https://getlawhand.com",
    ) as client:
        transport_response = await client.post(
            "/api/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        jwks_response = await client.get("/api/research-mcp/oauth/jwks")

    assert transport_response.status_code == 404
    assert jwks_response.status_code == 404


@pytest.mark.asyncio
async def test_research_refresh_uses_separate_namespace_and_resource(monkeypatch):
    captured = {}

    async def issue(_request, **kwargs):
        captured.update(kwargs)
        return "rmr_test"

    monkeypatch.setattr(oauth, "issue_refresh_token", issue)
    result = await oauth.issue_research_refresh_token(
        SimpleNamespace(),
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        client_id="research.client",
        grant_id=uuid.uuid4(),
        scopes=frozenset({"research:read"}),
    )

    assert result == "rmr_test"
    assert captured["namespace"] == "research_mcp"
    assert captured["token_prefix"] == "rmr_"
    assert captured["resource"] == "https://research.getlawhand.com/api/mcp"


@pytest.mark.asyncio
async def test_research_oauth_redis_and_grant_wrappers_are_namespaced(monkeypatch):
    request = SimpleNamespace()
    payload = {"state": "s"}
    monkeypatch.setattr(
        oauth, "save_authorization_request", AsyncMock(return_value="r")
    )
    monkeypatch.setattr(
        oauth, "load_authorization_request", AsyncMock(return_value=payload)
    )
    monkeypatch.setattr(
        oauth, "claim_authorization_request", AsyncMock(return_value=payload)
    )
    monkeypatch.setattr(oauth, "restore_authorization_request", AsyncMock())
    monkeypatch.setattr(oauth, "finalize_authorization_request", AsyncMock())
    monkeypatch.setattr(oauth, "save_authorization_code", AsyncMock(return_value="c"))
    monkeypatch.setattr(
        oauth, "load_authorization_code", AsyncMock(return_value=payload)
    )
    monkeypatch.setattr(
        oauth, "consume_authorization_code", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(oauth, "delete_authorization_code", AsyncMock())
    monkeypatch.setattr(
        oauth, "consume_refresh_token", AsyncMock(return_value=("missing", None))
    )
    monkeypatch.setattr(oauth, "revoke_refresh_family", AsyncMock())
    monkeypatch.setattr(oauth, "revoke_grant_refresh_tokens", AsyncMock())

    assert await oauth.save_research_authorization_request(request, payload) == "r"
    assert await oauth.load_research_authorization_request(request, "r") == payload
    assert await oauth.claim_research_authorization_request(request, "r") == payload
    await oauth.restore_research_authorization_request(request, "r")
    await oauth.finalize_research_authorization_request(request, "r")
    assert await oauth.save_research_authorization_code(request, payload) == "c"
    assert await oauth.load_research_authorization_code(request, "c") == payload
    assert await oauth.consume_research_authorization_code(request, "c", payload)
    await oauth.delete_research_authorization_code(request, "c")
    assert await oauth.consume_research_refresh_token(
        request,
        "rmr_x",
        expected_client_id="research.client",
        expected_resource=oauth.research_resource_uri(),
    ) == ("missing", None)
    await oauth.revoke_research_refresh_family(request, "family")
    await oauth.revoke_research_grant_refresh_tokens(request, uuid.uuid4())

    for mocked in (
        oauth.save_authorization_request,
        oauth.load_authorization_request,
        oauth.claim_authorization_request,
        oauth.restore_authorization_request,
        oauth.finalize_authorization_request,
        oauth.save_authorization_code,
        oauth.load_authorization_code,
        oauth.consume_authorization_code,
        oauth.delete_authorization_code,
        oauth.consume_refresh_token,
        oauth.revoke_refresh_family,
        oauth.revoke_grant_refresh_tokens,
    ):
        assert mocked.await_args.kwargs["namespace"] == "research_mcp"


@pytest.mark.asyncio
async def test_research_grant_wrapper_enforces_product_binding(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    scopes = frozenset({oauth.RESEARCH_SCOPE})
    assert oauth.research_client_id("client") == "research.client"
    assert oauth.research_client_id("research.client") == "research.client"

    active = SimpleNamespace(consent_version=oauth.CONSENT_VERSION)
    replace = AsyncMock(return_value=active)
    monkeypatch.setattr(oauth, "replace_active_grant", replace)
    assert (
        await oauth.replace_active_research_grant(
            object(),
            tenant_id=tenant_id,
            user_id=user_id,
            client=SimpleNamespace(client_id="research.client"),
        )
        is active
    )
    assert replace.await_args.kwargs["scopes"] == scopes
    assert replace.await_args.kwargs["consent_version"] == oauth.CONSENT_VERSION

    require = AsyncMock(return_value=active)
    monkeypatch.setattr(oauth, "require_active_workspace_grant", require)
    assert (
        await oauth.require_active_research_grant(
            object(),
            grant_id=str(grant_id),
            tenant_id=tenant_id,
            user_id=user_id,
            client_id="research.client",
            scopes=scopes,
        )
        is active
    )

    with pytest.raises(oauth.WorkspaceMCPGrantError, match="binding"):
        await oauth.require_active_research_grant(
            object(),
            grant_id=str(grant_id),
            tenant_id=tenant_id,
            user_id=user_id,
            client_id="workspace.client",
            scopes=scopes,
        )

    require.return_value = SimpleNamespace(consent_version="workspace-mcp-v1")
    with pytest.raises(oauth.WorkspaceMCPGrantError, match="unavailable"):
        await oauth.require_active_research_grant(
            object(),
            grant_id=str(grant_id),
            tenant_id=tenant_id,
            user_id=user_id,
            client_id="research.client",
            scopes=scopes,
        )


def test_research_consent_is_independent_of_workspace_license():
    request = SimpleNamespace(
        url=SimpleNamespace(path="/api/research-mcp/oauth/requests/x"), method="GET"
    )
    assert _is_license_exempt(request)
    token_admin = SimpleNamespace(
        url=SimpleNamespace(path="/api/mcp/product-keys"), method="POST"
    )
    assert _is_license_exempt(token_admin)
    tool_call = SimpleNamespace(
        url=SimpleNamespace(path="/api/mcp/tools/call"), method="POST"
    )
    assert not _is_license_exempt(tool_call)
    lookalike = SimpleNamespace(
        url=SimpleNamespace(path="/api/mcp/product-keys-other"), method="GET"
    )
    assert not _is_license_exempt(lookalike)


@pytest.mark.asyncio
async def test_research_bearer_builds_user_grant_principal(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    claims = {
        "tenant_id": str(tenant_id),
        "sub": str(user_id),
        "grant_id": str(grant_id),
        "client_id": "research.client",
        "jti": "token-id",
        "scope": "research:read",
    }
    monkeypatch.setattr(mcp_protocol, "decode_research_access_token", lambda _: claims)
    monkeypatch.setattr(mcp_protocol, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(mcp_protocol, "require_active_research_grant", AsyncMock())
    monkeypatch.setattr(mcp_protocol, "ensure_mcp_product_access", lambda _: None)

    user = SimpleNamespace(is_active=True)
    tenant = SimpleNamespace(id=tenant_id)

    class DB:
        def __init__(self):
            self.values = iter((user, tenant))

        async def scalar(self, _query):
            return next(self.values)

        async def commit(self):
            return None

    class Session:
        async def __aenter__(self):
            return DB()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(mcp_protocol, "async_session_maker", lambda: Session())
    redis = SimpleNamespace(exists=AsyncMock(return_value=0))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))

    identity = await mcp_protocol._authenticate_research_bearer(request, "token")

    assert identity.auth_type == "research_oauth"
    assert identity.product_key_id is None
    assert identity.oauth_grant_id == str(grant_id)
    assert identity.user_id == str(user_id)
    assert identity.allowed_tools == frozenset(mcp_protocol.DEFAULT_ALLOWED_TOOLS)


class _Result:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _DB:
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
        return _Result(self.rows)

    def add(self, value):
        self.added.append(value)

    async def _commit(self):
        for value in self.added:
            if getattr(value, "created_at", None) is None:
                value.created_at = datetime.now(timezone.utc)


def _request(**values):
    return SimpleNamespace(
        state=SimpleNamespace(request_id="rid"),
        headers={"content-type": "application/x-www-form-urlencoded"},
        app=SimpleNamespace(state=SimpleNamespace(redis=None)),
        form=AsyncMock(return_value=values),
        json=AsyncMock(return_value=values),
    )


def _client(client_id="research.client", redirects=None):
    redirects = redirects or ["https://app.example/callback"]
    return SimpleNamespace(
        client_id=client_id,
        client_name="Research client",
        redirect_uris=redirects,
        redirect_uri_set=set(redirects),
        grant_types=["authorization_code", "refresh_token"],
        is_active=lambda: True,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=2),
        last_used_at=None,
    )


def _user():
    tenant = SimpleNamespace(name="Acme", is_active=True)
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        tenant=tenant,
        email="u@example.com",
        full_name="Research User",
        is_active=True,
    )


def _grant(user, client_id="research.client", status="active", expires=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        client_id=client_id,
        client_name="Research client",
        status=status,
        expires_at=expires or datetime.now(timezone.utc) + timedelta(days=1),
        created_at=datetime.now(timezone.utc),
        last_used_at=None,
        revoked_at=None,
        revoked_by_user_id=None,
        revocation_reason=None,
    )


@pytest.mark.asyncio
async def test_research_router_gate_discovery_and_registration_errors(monkeypatch):
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", False)
    with pytest.raises(HTTPException):
        await router.research_jwks_endpoint()
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(router.settings, "RESEARCH_MCP_OAUTH_ENABLED", True)
    monkeypatch.setattr(
        router.settings, "RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED", False
    )
    response = await router.register_research_client(_request(), _DB())
    assert response.status_code == 403 and b"invalid_client_metadata" in response.body
    monkeypatch.setattr(
        router.settings, "RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED", True
    )
    db = _DB()
    response = await router.register_research_client(
        _request(client_name="Desktop", redirect_uris=["https://x/c"]), db
    )
    assert response.status_code == 201 and db.added[0].client_id.startswith("research.")

    form_request = _request(
        client_name="Claude",
        redirect_uris='["https://claude.ai/api/mcp/auth_callback"]',
        grant_types='["authorization_code"]',
        response_types='["code"]',
        token_endpoint_auth_method="none",
    )
    form_request.json = AsyncMock(side_effect=ValueError("not JSON"))
    form_db = _DB()
    form_response = await router.register_research_client(form_request, form_db)
    assert form_response.status_code == 201
    assert form_db.added[0].redirect_uris == ["https://claude.ai/api/mcp/auth_callback"]

    for form in (
        {},
        {"client_name": "x", "redirect_uris": ["https://x/c", "https://x/c"]},
        {
            "client_name": "x",
            "redirect_uris": ["https://x/c"],
            "grant_types": ["client_credentials"],
        },
        {
            "client_name": "x",
            "redirect_uris": ["https://x/c"],
            "grant_types": [{}],
        },
    ):
        response = await router.register_research_client(_request(**form), _DB())
        assert response.status_code == 400
    malformed = _request()
    malformed.json = AsyncMock(side_effect=ValueError("invalid json"))
    response = await router.register_research_client(malformed, _DB())
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_research_authorization_start_and_consent_details(monkeypatch):
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(router.settings, "RESEARCH_MCP_OAUTH_ENABLED", True)
    c, u = _client(), _user()
    monkeypatch.setattr(router, "_active_client", AsyncMock(return_value=c))
    save_request = AsyncMock(return_value="req-1")
    monkeypatch.setattr(router, "save_research_authorization_request", save_request)
    monkeypatch.setattr(router, "validate_pkce_challenge", lambda *_: None)
    monkeypatch.setattr(router, "ensure_mcp_product_access", lambda _: None)
    out = await router.begin_research_authorization(
        _request(),
        "code",
        c.client_id,
        c.redirect_uris[0],
        f"{router.RESEARCH_SCOPE} offline_access",
        "state",
        "a" * 43,
        "S256",
        router.research_resource_uri(),
        _DB(),
    )
    assert out.status_code == 302 and "request_id=req-1" in out.headers["location"]
    assert save_request.await_args.args[1]["scopes"] == [
        "offline_access",
        router.RESEARCH_SCOPE,
    ]
    monkeypatch.setattr(
        router, "load_research_authorization_request", AsyncMock(return_value=None)
    )
    with pytest.raises(HTTPException) as exc:
        await router.get_research_authorization_request("x", _request(), u, _DB())
    assert exc.value.status_code == 410
    monkeypatch.setattr(
        router,
        "load_research_authorization_request",
        AsyncMock(
            return_value={
                "client_id": c.client_id,
                "scopes": [router.RESEARCH_SCOPE, "offline_access"],
            }
        ),
    )
    details = await router.get_research_authorization_request("x", _request(), u, _DB())
    assert details["client"]["id"] == c.client_id
    assert {item["name"] for item in details["scopes"]} == {
        router.RESEARCH_SCOPE,
        "offline_access",
    }
    monkeypatch.setattr(
        router,
        "load_research_authorization_request",
        AsyncMock(return_value={"client_id": c.client_id, "scopes": ["matters:read"]}),
    )
    with pytest.raises(HTTPException) as exc:
        await router.get_research_authorization_request("x", _request(), u, _DB())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_research_consent_deny_approve_and_restore(monkeypatch):
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(router.settings, "RESEARCH_MCP_OAUTH_ENABLED", True)
    c, u = _client(), _user()
    pending = {
        "client_id": c.client_id,
        "redirect_uri": c.redirect_uris[0],
        "scopes": [router.RESEARCH_SCOPE, "offline_access"],
        "state": "s",
        "code_challenge": "cc",
    }
    monkeypatch.setattr(router, "ensure_mcp_product_access", lambda _: None)
    monkeypatch.setattr(
        router, "claim_research_authorization_request", AsyncMock(return_value=pending)
    )
    monkeypatch.setattr(router, "_active_client", AsyncMock(return_value=c))
    monkeypatch.setattr(router, "finalize_research_authorization_request", AsyncMock())
    denied = await router.decide_research_authorization(
        "r", router.ConsentDecision(approved=False), _request(), u, _DB()
    )
    assert parse_qs(urlsplit(denied["redirect_to"]).query)["error"] == ["access_denied"]
    g = _grant(u)
    monkeypatch.setattr(
        router, "replace_active_research_grant", AsyncMock(return_value=g)
    )
    save_code = AsyncMock(return_value="code")
    monkeypatch.setattr(router, "save_research_authorization_code", save_code)
    approved = await router.decide_research_authorization(
        "r", router.ConsentDecision(approved=True), _request(), u, _DB()
    )
    assert "code=code" in approved["redirect_to"]
    assert save_code.await_args.args[1]["scopes"] == [router.RESEARCH_SCOPE]
    restore = AsyncMock()
    monkeypatch.setattr(
        router,
        "replace_active_research_grant",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(router, "restore_research_authorization_request", restore)
    with pytest.raises(RuntimeError):
        await router.decide_research_authorization(
            "r", router.ConsentDecision(approved=True), _request(), u, _DB()
        )
    assert restore.await_count == 1


@pytest.mark.asyncio
async def test_research_token_code_refresh_replay_and_invalid_requests(monkeypatch):
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(router.settings, "RESEARCH_MCP_OAUTH_ENABLED", True)
    c, u, g = _client(), _user(), None
    db = _DB(c)
    monkeypatch.setattr(router, "_active_client", AsyncMock(return_value=c))
    monkeypatch.setattr(
        router, "load_research_authorization_code", AsyncMock(return_value=None)
    )
    out = await router.research_token(
        _request(
            grant_type="authorization_code",
            client_id=c.client_id,
            resource=router.research_resource_uri(),
            code="x",
        ),
        db,
    )
    assert out.status_code == 400
    payload = {
        "client_id": c.client_id,
        "redirect_uri": c.redirect_uris[0],
        "resource": router.research_resource_uri(),
        "code_challenge": "cc",
        "tenant_id": str(u.tenant_id),
        "user_id": str(u.id),
        "grant_id": str(uuid.uuid4()),
        "scopes": [router.RESEARCH_SCOPE],
    }
    g = _grant(u)
    monkeypatch.setattr(
        router, "load_research_authorization_code", AsyncMock(return_value=payload)
    )
    monkeypatch.setattr(router, "verify_pkce", lambda *_: True)
    monkeypatch.setattr(router, "_load_actor", AsyncMock(return_value=(g, u)))
    monkeypatch.setattr(
        router, "consume_research_authorization_code", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        router, "mint_research_access_token", lambda **_: ("at", "tid", 60)
    )
    monkeypatch.setattr(
        router, "issue_research_refresh_token", AsyncMock(return_value="rt")
    )
    out = await router.research_token(
        _request(
            grant_type="authorization_code",
            client_id=c.client_id,
            resource=router.research_resource_uri(),
            code="x",
            redirect_uri=c.redirect_uris[0],
            code_verifier="v",
        ),
        db,
    )
    assert out.status_code == 200 and b'"access_token":"at"' in out.body
    monkeypatch.setattr(
        router,
        "consume_research_refresh_token",
        AsyncMock(return_value=("replay", "fam")),
    )
    revoke = AsyncMock()
    monkeypatch.setattr(router, "revoke_research_refresh_family", revoke)
    out = await router.research_token(
        _request(
            grant_type="refresh_token",
            client_id=c.client_id,
            resource=router.research_resource_uri(),
            refresh_token="rmr_x",
        ),
        db,
    )
    assert out.status_code == 400 and revoke.await_count == 1
    out = await router.research_token(
        _request(
            grant_type="nonsense",
            client_id=c.client_id,
            resource=router.research_resource_uri(),
        ),
        db,
    )
    assert out.status_code == 400


@pytest.mark.asyncio
async def test_research_refresh_binding_is_checked_atomically(monkeypatch):
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(router.settings, "RESEARCH_MCP_OAUTH_ENABLED", True)
    c = _client()
    db = _DB(c)
    monkeypatch.setattr(router, "_active_client", AsyncMock(return_value=c))
    consume = AsyncMock(return_value=("none", None))
    monkeypatch.setattr(router, "consume_research_refresh_token", consume)
    revoke = AsyncMock()
    monkeypatch.setattr(router, "revoke_research_refresh_family", revoke)
    result = await router.research_token(
        _request(
            grant_type="refresh_token",
            client_id=c.client_id,
            resource=router.research_resource_uri(),
            refresh_token="rmr_wrong-client",
        ),
        db,
    )
    assert result.status_code == 400
    assert revoke.await_count == 0
    assert consume.await_args.kwargs.get("expected_client_id") == c.client_id
    assert (
        consume.await_args.kwargs.get("expected_resource")
        == router.research_resource_uri()
    )


@pytest.mark.asyncio
async def test_research_revoke_access_refresh_and_grant_lifecycle(monkeypatch):
    monkeypatch.setattr(router.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(router.settings, "RESEARCH_MCP_OAUTH_ENABLED", True)
    c, u = _client(), _user()
    g = _grant(u, expires=datetime.now(timezone.utc) - timedelta(seconds=1))
    monkeypatch.setattr(router, "_active_client", AsyncMock(return_value=c))
    monkeypatch.setattr(
        router,
        "consume_research_refresh_token",
        AsyncMock(return_value=("missing", None)),
    )
    out = await router.revoke_research_token(
        _request(token="rmr_x", client_id=c.client_id), _DB()
    )
    assert out.status_code == 200

    redis = SimpleNamespace(setex=AsyncMock())
    access_request = _request(token="access-token", client_id=c.client_id)
    access_request.app.state.redis = redis
    monkeypatch.setattr(
        router,
        "decode_research_access_token",
        lambda _token: {"client_id": c.client_id, "jti": "token-id"},
    )
    out = await router.revoke_research_token(access_request, _DB())
    assert out.status_code == 200
    redis.setex.assert_awaited_once()

    db = _DB(rows=[g])
    monkeypatch.setattr(router, "ensure_mcp_product_access", lambda _: None)
    listing = await router.list_research_grants(u, db)
    assert listing["items"][0]["status"] == "expired" and db.commit.await_count == 1
    g.status = "active"
    db = _DB(g)
    cleanup = AsyncMock()
    monkeypatch.setattr(router, "revoke_research_grant_refresh_tokens", cleanup)
    result = await router.revoke_research_grant(
        g.id, router.RevokeGrantRequest(reason="bye"), _request(), u, db
    )
    assert result["status"] == "revoked" and cleanup.await_count == 1


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _QuotaDB:
    def __init__(self, used):
        self.used = used
        self.statements = []

    async def execute(self, statement, *_args, **_kwargs):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return SimpleNamespace()
        return _ScalarResult(self.used)


@pytest.mark.asyncio
async def test_research_oauth_quota_is_tenant_user_bound(monkeypatch):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    monkeypatch.setattr(mcp_product.settings, "MCP_DEFAULT_MONTHLY_CALL_LIMIT", 2)

    allowed = _QuotaDB(used=1)
    await mcp_product.enforce_research_oauth_quota(
        allowed, tenant_id=tenant_id, user_id=user_id
    )
    quota_query = str(allowed.statements[1])
    assert "mcp_usage_events.tenant_id" in quota_query
    assert "mcp_usage_events.user_id" in quota_query
    assert "mcp_usage_events.auth_type" in quota_query

    with pytest.raises(HTTPException) as exc:
        await mcp_product.enforce_research_oauth_quota(
            _QuotaDB(used=2), tenant_id=tenant_id, user_id=user_id
        )
    assert exc.value.status_code == 429


class _UsageDB:
    def __init__(self, stripe_customer_id="cus_research"):
        self.tenant = SimpleNamespace(stripe_customer_id=stripe_customer_id)
        self.added = []
        self.commits = 0

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()

    async def scalar(self, _query):
        return self.tenant

    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace()

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_successful_research_oauth_usage_enqueues_stripe_meter(monkeypatch):
    db = _UsageDB()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    queued = {}

    async def enqueue(_db, **kwargs):
        queued.update(kwargs)
        return SimpleNamespace(max_attempts=1)

    monkeypatch.setattr(mcp_product, "enqueue_job", enqueue)
    event = await mcp_product.record_mcp_usage(
        db=db,
        tenant_id=tenant_id,
        user_id=user_id,
        oauth_grant_id=grant_id,
        product_key_id=None,
        auth_type="research_oauth",
        transport="streamable_http",
        tool_name="search_caselaw",
        status_code=200,
        result_count=3,
    )

    assert event.oauth_grant_id == grant_id
    assert event.product_key_id is None
    assert queued["kind"] == "mcp_stripe_meter"
    assert queued["payload"]["usage_event_id"] == str(event.id)
    assert queued["payload"]["stripe_customer_id"] == "cus_research"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_failed_research_oauth_usage_is_observable_but_not_metered(monkeypatch):
    db = _UsageDB()
    enqueue = AsyncMock()
    monkeypatch.setattr(mcp_product, "enqueue_job", enqueue)

    event = await mcp_product.record_mcp_usage(
        db=db,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        oauth_grant_id=uuid.uuid4(),
        product_key_id=None,
        auth_type="research_oauth",
        tool_name="search_caselaw",
        status_code=500,
    )

    assert event.status_code == 500
    enqueue.assert_not_awaited()
    assert db.commits == 1
