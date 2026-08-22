import base64
import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.services.workspace_mcp_oauth import (
    WorkspaceOAuthError,
    append_workspace_mcp_audit,
    normalized_scopes,
    consume_authorization_code,
    load_authorization_code,
    save_authorization_code,
    validate_pkce_challenge,
    validate_redirect_uri,
    verify_pkce,
)


def test_redirect_uri_allows_https_and_loopback_only():
    assert (
        validate_redirect_uri("https://client.example/callback")
        == "https://client.example/callback"
    )
    assert (
        validate_redirect_uri("http://127.0.0.1:43123/callback")
        == "http://127.0.0.1:43123/callback"
    )
    assert (
        validate_redirect_uri("http://[::1]:43123/callback")
        == "http://[::1]:43123/callback"
    )


@pytest.mark.parametrize(
    "uri",
    [
        "http://client.example/callback",
        "https://client.example/callback#fragment",
        "https://user:password@client.example/callback",
        "javascript://client.example/callback",
    ],
)
def test_redirect_uri_rejects_non_public_or_ambiguous_targets(uri):
    with pytest.raises(WorkspaceOAuthError):
        validate_redirect_uri(uri)


def test_pkce_requires_s256_and_verifier_binds_to_challenge():
    verifier = "a" * 64

    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    validate_pkce_challenge(challenge, "S256")
    assert verify_pkce(verifier, challenge)
    assert not verify_pkce("b" * 64, challenge)
    with pytest.raises(WorkspaceOAuthError, match="S256"):
        validate_pkce_challenge(challenge, "plain")


def test_scopes_are_canonicalized_and_unknown_scopes_fail_closed():
    assert normalized_scopes(
        "tasks:read matters:read documents:read templates:read tasks:read"
    ) == frozenset({"tasks:read", "matters:read", "documents:read", "templates:read"})
    with pytest.raises(WorkspaceOAuthError, match="invalid"):
        normalized_scopes("matters:read admin:all")


class _AuthorizationCodeRedis:
    def __init__(self):
        self.values = {}

    async def setex(self, key, _ttl, value):
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def eval(self, _script, _key_count, key, expected):
        if self.values.get(key) != expected:
            return 0
        del self.values[key]
        return 1


@pytest.mark.asyncio
async def test_authorization_code_is_consumed_only_after_exact_payload_validation():
    redis = _AuthorizationCodeRedis()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
    payload = {
        "client_id": "desktop-client",
        "redirect_uri": "http://127.0.0.1:43123/callback",
        "resource": "https://lawhand.example/api/mcp/workspace",
        "code_challenge": "challenge",
    }

    code = await save_authorization_code(request, payload)
    assert await load_authorization_code(request, code) == payload

    mismatched = {**payload, "client_id": "attacker-client"}
    assert not await consume_authorization_code(request, code, mismatched)
    assert await load_authorization_code(request, code) == payload

    assert await consume_authorization_code(request, code, payload)
    assert await load_authorization_code(request, code) is None


class _AuditDB:
    def __init__(self):
        self.event = None

    async def execute(self, *_args, **_kwargs):
        return None

    async def scalar(self, *_args, **_kwargs):
        return None

    def add(self, event):
        self.event = event

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_workspace_audit_hash_matches_all_persisted_evidence_fields():
    db = _AuditDB()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/mcp/workspace",
            "query_string": b"",
            "headers": [
                (b"x-request-id", b"request-123"),
                (b"user-agent", b"Desktop-Harness/1.0"),
            ],
            "client": ("203.0.113.7", 43123),
            "server": ("lawhand.example", 443),
            "scheme": "https",
        }
    )

    event = await append_workspace_mcp_audit(
        db,
        request,
        tenant_id=tenant_id,
        user_id=user_id,
        grant_id=grant_id,
        client_id="c" * 250,
        event_type="e" * 100,
        tool_name="t" * 150,
        outcome="success",
        metadata={"scopes": ["matters:read"]},
    )

    persisted_payload = {
        "id": str(event.id),
        "tenant_id": str(event.tenant_id),
        "user_id": str(event.user_id),
        "grant_id": str(event.grant_id),
        "client_id": event.client_id,
        "event_type": event.event_type,
        "tool_name": event.tool_name,
        "outcome": event.outcome,
        "request_id": event.request_id,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "metadata": event.metadata_json,
        "chain_position": event.chain_position,
        "prev_event_hash": event.prev_event_hash,
        "created_at": event.created_at.isoformat(),
    }
    reconstructed_hash = hashlib.sha256(
        json.dumps(persisted_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert db.event is event
    assert event.client_id == "c" * 200
    assert event.event_type == "e" * 80
    assert event.tool_name == "t" * 120
    assert event.request_id == "request-123"
    assert event.ip_address == "203.0.113.7"
    assert event.user_agent == "Desktop-Harness/1.0"
    assert reconstructed_hash == event.event_hash
