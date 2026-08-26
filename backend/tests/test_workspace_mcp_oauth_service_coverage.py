import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import workspace_mcp_oauth as oauth


def _request(redis=None, headers=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=redis)),
        state=SimpleNamespace(request_id="req-1"),
        headers=headers or {"user-agent": "coverage-agent"},
    )


def test_redirect_pkce_and_scopes_reject_bad_inputs():
    for value in (
        "",
        "http://example.test",
        "https://u:p@example.test/cb",
        "https://x/cb#f",
    ):
        with pytest.raises(oauth.WorkspaceOAuthError):
            oauth.validate_redirect_uri(value)
    with pytest.raises(oauth.WorkspaceOAuthError):
        oauth.validate_pkce_challenge("a" * 43, "plain")
    with pytest.raises(oauth.WorkspaceOAuthError):
        oauth.validate_pkce_challenge("!" * 43, "S256")
    with pytest.raises(oauth.WorkspaceOAuthError):
        oauth.normalized_scopes("")


class _RegistrationForm:
    def __init__(self, values):
        self.values = values

    def get(self, field, default=None):
        values = self.values.get(field, [])
        return values[-1] if values else default

    def getlist(self, field):
        return self.values.get(field, [])


@pytest.mark.asyncio
async def test_registration_payload_json_and_form_compatibility():
    json_payload = {
        "client_name": "Claude",
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    }
    json_request = SimpleNamespace(json=AsyncMock(return_value=json_payload))
    assert await oauth.parse_dynamic_client_registration_payload(json_request) == (
        json_payload
    )

    non_object = SimpleNamespace(json=AsyncMock(return_value=[]))
    with pytest.raises(oauth.WorkspaceOAuthError, match="must be an object"):
        await oauth.parse_dynamic_client_registration_payload(non_object)

    form = _RegistrationForm(
        {
            "client_name": ["Claude"],
            "redirect_uris[]": ["https://claude.com/api/mcp/auth_callback"],
            "grant_types": ['["authorization_code"]'],
            "response_types": ["code"],
            "token_endpoint_auth_method": ["none"],
            "application_type": ["web"],
        }
    )
    form_request = SimpleNamespace(
        json=AsyncMock(side_effect=ValueError("not JSON")),
        form=AsyncMock(return_value=form),
    )
    parsed = await oauth.parse_dynamic_client_registration_payload(form_request)
    assert parsed == {
        "client_name": "Claude",
        "token_endpoint_auth_method": "none",
        "application_type": "web",
        "redirect_uris": ["https://claude.com/api/mcp/auth_callback"],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    }

    invalid_form = SimpleNamespace(
        json=AsyncMock(side_effect=ValueError("not JSON")),
        form=AsyncMock(side_effect=ValueError("not form data")),
    )
    with pytest.raises(oauth.WorkspaceOAuthError, match="valid JSON or form data"):
        await oauth.parse_dynamic_client_registration_payload(invalid_form)


def test_pkce_verifier_ascii_and_length_fail_closed():
    assert not oauth.verify_pkce("short", "a" * 43)
    assert not oauth.verify_pkce("é" * 64, "a" * 43)


def test_jwks_and_rs256_key_selection(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(
        oauth.settings,
        "WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64",
        base64.b64encode(private_pem).decode(),
    )
    monkeypatch.setattr(
        oauth.settings,
        "WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64",
        base64.b64encode(public_pem).decode(),
    )
    monkeypatch.setattr(
        oauth.settings, "WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON", json.dumps([])
    )
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_SIGNING_KEY_ID", "current")
    assert oauth.workspace_signing_algorithm() == "RS256"
    jwks = oauth.workspace_jwks()
    assert jwks["keys"][0]["kid"] == "current"
    assert jwks["keys"][0]["alg"] == "RS256"
    token, _, _ = oauth.mint_workspace_access_token(
        user_id=uuid4(),
        tenant_id=uuid4(),
        client_id="coverage-client",
        grant_id=uuid4(),
        scopes=frozenset({"matters:read"}),
    )
    verification_key, algorithm = oauth.workspace_verification_key(token)
    assert algorithm == "RS256"
    assert b"BEGIN PUBLIC KEY" in verification_key


def test_hs256_access_token_claims_and_decode(monkeypatch):
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64", "")
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64", "")
    monkeypatch.setattr(
        oauth.settings, "WORKSPACE_MCP_TOKEN_SIGNING_KEY", "test-signing-key"
    )
    user_id, tenant_id, grant_id = uuid4(), uuid4(), uuid4()
    token, token_id, expires = oauth.mint_workspace_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        client_id="coverage-client",
        grant_id=grant_id,
        scopes=frozenset({"tasks:read", "matters:read"}),
    )
    assert token and token_id and expires > 0
    claims = oauth.jwt.get_unverified_claims(token)
    assert claims["type"] == "workspace_mcp"
    assert claims["scope"] == "matters:read tasks:read"


def test_bounded_audit_metadata_filters_secrets_and_limits_size():
    cleaned = oauth._bounded_audit_metadata(
        {
            "Authorization": "secret",
            "normal": "ok",
            "items": list(range(30)),
            "none": None,
        }
    )
    assert "Authorization" not in cleaned
    assert cleaned["normal"] == "ok"
    assert len(cleaned["items"]) == 25
    with pytest.raises(ValueError):
        oauth._bounded_audit_metadata(
            {f"field-{index}": "z" * 500 for index in range(20)}
        )


def test_resource_and_tenant_helpers(monkeypatch):
    legacy = "https://lawhand.test/api/mcp/workspace"
    canonical = "https://mcp.lawhand.test/api/mcp/workspace"
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_RESOURCE", legacy)
    monkeypatch.setattr(
        oauth.settings,
        "WORKSPACE_MCP_CANONICAL_RESOURCE",
        f"{canonical}/",
    )
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_RESOURCE_ALIASES", "")
    monkeypatch.setattr(oauth.settings, "WORKSPACE_MCP_ISSUER", "https://lawhand.test/")
    assert oauth.workspace_resource_uri() == canonical
    assert oauth.workspace_resource_uris() == frozenset({canonical, legacy})
    assert oauth.workspace_resource_is_allowed(canonical)
    assert oauth.workspace_resource_is_allowed(legacy)
    assert not oauth.workspace_resource_is_allowed(
        "https://attacker.invalid/api/mcp/workspace"
    )
    assert oauth.workspace_protected_resource_metadata_uri() == (
        "https://mcp.lawhand.test"
        "/.well-known/oauth-protected-resource/api/mcp/workspace"
    )
    assert oauth.workspace_issuer_uri() == "https://lawhand.test"
    assert oauth.workspace_tenant_allowed("tenant-a")
    assert oauth.workspace_tenant_allowed("tenant-c")
    assert oauth.require_workspace_tenant_allowed("tenant-c") is None


@pytest.mark.asyncio
async def test_redis_required_for_authorization_state():
    request = _request()
    with pytest.raises(Exception):
        await oauth.save_authorization_request(request, {"state": "x"})


class _RuntimeRedis:
    def __init__(self):
        self.eval_calls = []
        self.events = []

    async def eval(self, script, key_count, *args):
        self.eval_calls.append((script, key_count, args))
        self.events.append(("eval", key_count, args))
        return 1

    async def setex(self, key, ttl, value):
        self.events.append(("setex", key, ttl, value))

    async def smembers(self, key):
        self.events.append(("smembers", key))
        return set()

    async def delete(self, key):
        self.events.append(("delete", key))


@pytest.mark.asyncio
async def test_refresh_issuance_checks_grant_marker_and_runtime_cleanup_sets_it_first():
    redis = _RuntimeRedis()
    request = _request(redis=redis)
    grant_id = uuid4()

    refresh = await oauth.issue_refresh_token(
        request,
        user_id=uuid4(),
        tenant_id=uuid4(),
        client_id="desktop-client",
        grant_id=grant_id,
        scopes=frozenset({"matters:read"}),
    )

    assert refresh.startswith("wmr_")
    _script, key_count, args = redis.eval_calls[0]
    assert key_count == 5
    assert f"workspace_mcp_grant:{grant_id}" in args

    research_grant_id = uuid4()
    await oauth.issue_refresh_token(
        request,
        user_id=uuid4(),
        tenant_id=uuid4(),
        client_id="research.desktop-client",
        grant_id=research_grant_id,
        scopes=frozenset({"research:read"}),
        namespace="research_mcp",
        resource="https://research.lawhand.test/api/mcp",
    )
    assert f"research_mcp:grant_revoked:{research_grant_id}" in redis.eval_calls[1][2]

    redis.events.clear()
    await oauth.revoke_workspace_grant_runtime(request, grant_id)

    assert redis.events[0][0:2] == (
        "setex",
        f"workspace_mcp_grant:{grant_id}",
    )
    assert (
        "smembers",
        f"workspace_mcp:grant_refresh_families:{grant_id}",
    ) in redis.events
