"""OAuth 2.1/PKCE primitives for LawHand's user-bound workspace MCP."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.middleware.rate_limit import _client_ip
from app.models.workspace_mcp_audit import WorkspaceMCPAuditEvent
from app.models.workspace_mcp_client import WorkspaceMCPClient
from app.models.workspace_mcp_grant import WorkspaceMCPGrant

settings = get_settings()

CONSENT_VERSION = "workspace-mcp-v1"
CONSENT_NOTICE = (
    "LawHand workspace access is limited to the scopes shown. Proposal tools "
    "create review work only; they cannot approve, file, send, or deliver it."
)

WORKSPACE_SCOPE_LABELS: dict[str, str] = {
    "matters:read": "Find matters and read bounded matter context",
    "tasks:read": "Read matter task lists",
    "contacts:read": "Read enumerated matter recipients",
    "documents:read": "Read bounded matter document metadata and text",
    "templates:read": "Read active firm document template metadata",
    "tasks:propose": "Create tasks that start in human review",
    "communications:propose": "Draft client email proposals without sending",
    "documents:propose": "Create cloud-backed DOCX drafts for staged review",
}


class WorkspaceOAuthError(ValueError):
    """One safe OAuth protocol error."""

    def __init__(
        self,
        error: str,
        description: str,
        *,
        status_code: int = 400,
    ) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code


def workspace_resource_uri() -> str:
    return settings.workspace_mcp_endpoint


def workspace_resource_uris() -> frozenset[str]:
    return frozenset(
        {workspace_resource_uri(), *settings.workspace_mcp_legacy_resources}
    )


def workspace_resource_is_allowed(resource: object) -> bool:
    return isinstance(resource, str) and resource in workspace_resource_uris()


def workspace_protected_resource_metadata_uri() -> str:
    resource = urlsplit(workspace_resource_uri())
    return (
        f"{resource.scheme}://{resource.netloc}"
        f"/.well-known/oauth-protected-resource{resource.path}"
    )


def workspace_issuer_uri() -> str:
    return settings.WORKSPACE_MCP_ISSUER.rstrip("/")


def workspace_tenant_allowed(_tenant_id: uuid.UUID | str) -> bool:
    """Return native availability for compatibility with older adapters.

    Workspace MCP authorization is tenant-scoped by the authenticated user,
    OAuth grant, token, and RLS context. Tenant administrators control access
    per user; the retired deployment-time pilot allowlist no longer overrides
    that policy.
    """

    return True


def require_workspace_tenant_allowed(tenant_id: uuid.UUID | str) -> None:
    """Compatibility shim for callers written during the pilot rollout."""

    workspace_tenant_allowed(tenant_id)


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decoded_key(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def _current_public_key() -> bytes | str:
    if settings.WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64:
        return _decoded_key(settings.WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64)
    return settings.WORKSPACE_MCP_TOKEN_SIGNING_KEY


def _current_private_key() -> bytes | str:
    if settings.WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64:
        return _decoded_key(settings.WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64)
    return settings.WORKSPACE_MCP_TOKEN_SIGNING_KEY


def workspace_signing_algorithm() -> str:
    return "RS256" if settings.WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64 else "HS256"


def workspace_jwks() -> dict[str, list[dict[str, str]]]:
    entries: list[tuple[str, bytes]] = []
    if settings.WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64:
        entries.append(
            (
                settings.WORKSPACE_MCP_SIGNING_KEY_ID,
                _decoded_key(settings.WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64),
            )
        )
    for item in json.loads(settings.WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON or "[]"):
        entries.append((str(item["kid"]), _decoded_key(item["public_key_b64"])))

    keys: list[dict[str, str]] = []
    for kid, pem in entries:
        public_key = serialization.load_pem_public_key(pem)
        if not isinstance(public_key, rsa.RSAPublicKey):
            continue
        numbers = public_key.public_numbers()
        keys.append(
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        )
    return {"keys": keys}


def workspace_verification_key(token: str) -> tuple[bytes | str, str]:
    algorithm = workspace_signing_algorithm()
    if algorithm == "HS256":
        return _current_public_key(), algorithm
    try:
        header = jwt.get_unverified_header(token)
    except (JWTError, TypeError, ValueError) as exc:
        raise WorkspaceOAuthError(
            "invalid_token", "Access token header is invalid"
        ) from exc
    kid = str(header.get("kid") or "")
    if kid == settings.WORKSPACE_MCP_SIGNING_KEY_ID:
        return _current_public_key(), algorithm
    for item in json.loads(settings.WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON or "[]"):
        if secrets.compare_digest(str(item.get("kid") or ""), kid):
            return _decoded_key(str(item["public_key_b64"])), algorithm
    raise WorkspaceOAuthError("invalid_token", "Access token signing key is unknown")


def mint_workspace_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    client_id: str,
    grant_id: uuid.UUID,
    scopes: frozenset[str],
) -> tuple[str, str, int]:
    now = datetime.now(timezone.utc)
    expires_in = settings.WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES * 60
    token_id = str(uuid.uuid4())
    claims = {
        "iss": workspace_issuer_uri(),
        "aud": settings.WORKSPACE_MCP_AUDIENCE,
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "type": "workspace_mcp",
        "token_use": "access",
        "client_id": client_id,
        "grant_id": str(grant_id),
        "jti": token_id,
        "scope": " ".join(sorted(scopes)),
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    headers = None
    if workspace_signing_algorithm() == "RS256":
        headers = {"kid": settings.WORKSPACE_MCP_SIGNING_KEY_ID, "typ": "at+jwt"}
    encoded = jwt.encode(
        claims,
        _current_private_key(),
        algorithm=workspace_signing_algorithm(),
        headers=headers,
    )
    return encoded, token_id, expires_in


def validate_redirect_uri(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 1000:
        raise WorkspaceOAuthError("invalid_client_metadata", "Redirect URI is invalid")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise WorkspaceOAuthError(
            "invalid_client_metadata", "Redirect URI is invalid"
        ) from exc
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise WorkspaceOAuthError("invalid_client_metadata", "Redirect URI is invalid")
    host = (parsed.hostname or "").casefold()
    is_loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
        raise WorkspaceOAuthError(
            "invalid_client_metadata",
            "Redirect URIs must use HTTPS or an HTTP loopback address",
        )
    return value


def validate_pkce_challenge(challenge: str, method: str) -> None:
    if method != "S256":
        raise WorkspaceOAuthError("invalid_request", "S256 PKCE is required")
    if not 43 <= len(challenge) <= 128 or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
        for char in challenge
    ):
        raise WorkspaceOAuthError("invalid_request", "PKCE challenge is invalid")


def verify_pkce(verifier: str, challenge: str) -> bool:
    if not 43 <= len(verifier) <= 128:
        return False
    digest = hashlib.sha256(verifier.encode("ascii", errors="ignore")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, challenge)


def normalized_scopes(raw: str) -> frozenset[str]:
    scopes = frozenset(value for value in raw.split() if value)
    if not scopes or scopes - WORKSPACE_SCOPE_LABELS.keys():
        raise WorkspaceOAuthError(
            "invalid_scope", "Requested workspace scope is invalid"
        )
    return scopes


def _redis(request: Request):
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(
            status_code=503,
            detail="Workspace authorization state service is unavailable",
        )
    return redis


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_json(value: bytes | str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else None


async def save_authorization_request(request: Request, payload: dict[str, Any]) -> str:
    request_id = secrets.token_urlsafe(32)
    await _redis(request).setex(
        f"workspace_mcp:auth_request:{request_id}",
        settings.WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS,
        _json_bytes(payload),
    )
    return request_id


async def load_authorization_request(
    request: Request, request_id: str, *, consume: bool = False
) -> dict[str, Any] | None:
    key = f"workspace_mcp:auth_request:{request_id}"
    redis = _redis(request)
    raw = await redis.getdel(key) if consume else await redis.get(key)
    return _decode_json(raw)


_CLAIM_AUTHORIZATION_REQUEST_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return nil end
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 1 then return nil end
if redis.call('EXISTS', KEYS[2]) == 1 then return nil end
redis.call('PSETEX', KEYS[2], ttl, raw)
redis.call('DEL', KEYS[1])
return raw
"""


_RESTORE_AUTHORIZATION_REQUEST_SCRIPT = """
local raw = redis.call('GET', KEYS[2])
if not raw then return 0 end
local ttl = redis.call('PTTL', KEYS[2])
if ttl > 0 and redis.call('EXISTS', KEYS[1]) == 0 then
  redis.call('PSETEX', KEYS[1], ttl, raw)
end
redis.call('DEL', KEYS[2])
return 1
"""


async def claim_authorization_request(
    request: Request, request_id: str
) -> dict[str, Any] | None:
    redis = _redis(request)
    raw = await redis.eval(
        _CLAIM_AUTHORIZATION_REQUEST_SCRIPT,
        2,
        f"workspace_mcp:auth_request:{request_id}",
        f"workspace_mcp:auth_request_claim:{request_id}",
    )
    return _decode_json(raw)


async def restore_authorization_request(request: Request, request_id: str) -> None:
    redis = _redis(request)
    await redis.eval(
        _RESTORE_AUTHORIZATION_REQUEST_SCRIPT,
        2,
        f"workspace_mcp:auth_request:{request_id}",
        f"workspace_mcp:auth_request_claim:{request_id}",
    )


async def finalize_authorization_request(request: Request, request_id: str) -> None:
    await _redis(request).delete(f"workspace_mcp:auth_request_claim:{request_id}")


async def save_authorization_code(request: Request, payload: dict[str, Any]) -> str:
    code = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code.encode("ascii")).hexdigest()
    await _redis(request).setex(
        f"workspace_mcp:auth_code:{digest}",
        settings.WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS,
        _json_bytes(payload),
    )
    return code


async def delete_authorization_code(request: Request, code: str) -> None:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    await _redis(request).delete(f"workspace_mcp:auth_code:{digest}")


async def load_authorization_code(request: Request, code: str) -> dict[str, Any] | None:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    raw = await _redis(request).get(f"workspace_mcp:auth_code:{digest}")
    return _decode_json(raw)


_CONSUME_AUTHORIZATION_CODE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw or raw ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""


async def consume_authorization_code(
    request: Request, code: str, payload: dict[str, Any]
) -> bool:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    consumed = await _redis(request).eval(
        _CONSUME_AUTHORIZATION_CODE_SCRIPT,
        1,
        f"workspace_mcp:auth_code:{digest}",
        _json_bytes(payload),
    )
    return bool(consumed)


def _refresh_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_CONSUME_REFRESH_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if raw then
  local ttl = redis.call('TTL', KEYS[1])
  local payload = cjson.decode(raw)
  local family = payload['family_id']
  if redis.call('EXISTS', KEYS[4]) == 1 then
    redis.call('DEL', KEYS[1])
    redis.call('SREM', KEYS[3], ARGV[1])
    return {'revoked', family}
  end
  redis.call('DEL', KEYS[1])
  redis.call('SREM', KEYS[3], ARGV[1])
  if ttl < 1 then ttl = 1 end
  redis.call('SETEX', KEYS[2], ttl, family)
  return {'consumed', raw}
end
local family = redis.call('GET', KEYS[2])
if family then return {'replay', family} end
return {'missing', ''}
"""


_ISSUE_REFRESH_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('SETEX', KEYS[2], ARGV[1], ARGV[2])
redis.call('SADD', KEYS[3], ARGV[3])
redis.call('EXPIRE', KEYS[3], ARGV[1])
redis.call('SADD', KEYS[4], ARGV[4])
redis.call('EXPIRE', KEYS[4], ARGV[1])
return 1
"""


_REVOKE_REFRESH_FAMILY_SCRIPT = """
redis.call('SETEX', KEYS[1], ARGV[1], '1')
local members = redis.call('SMEMBERS', KEYS[2])
for _, token_hash in ipairs(members) do
  redis.call('DEL', ARGV[2] .. token_hash)
end
redis.call('DEL', KEYS[2])
return #members
"""


async def issue_refresh_token(
    request: Request,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    client_id: str,
    grant_id: uuid.UUID,
    scopes: frozenset[str],
    family_id: str | None = None,
) -> str:
    redis = _redis(request)
    family = family_id or str(uuid.uuid4())
    token = "wmr_" + secrets.token_urlsafe(64)
    token_hash = _refresh_hash(token)
    ttl = settings.WORKSPACE_MCP_REFRESH_TOKEN_DAYS * 86400
    payload = {
        "family_id": family,
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
        "client_id": client_id,
        "grant_id": str(grant_id),
        "scopes": sorted(scopes),
        "resource": workspace_resource_uri(),
        "expires_at": int(time.time()) + ttl,
    }
    issued = await redis.eval(
        _ISSUE_REFRESH_SCRIPT,
        4,
        f"workspace_mcp:refresh_family_revoked:{family}",
        f"workspace_mcp:refresh:{token_hash}",
        f"workspace_mcp:refresh_family:{family}",
        f"workspace_mcp:grant_refresh_families:{grant_id}",
        ttl,
        _json_bytes(payload),
        token_hash,
        family,
    )
    if not issued:
        raise WorkspaceOAuthError("invalid_grant", "Refresh token family is revoked")
    return token


async def consume_refresh_token(
    request: Request, token: str
) -> tuple[str, dict[str, Any] | str | None]:
    token_hash = _refresh_hash(token)
    redis = _redis(request)
    # The family set key is resolved after a bounded read; the Lua script still
    # makes token consumption, revoked-family rejection, and replay tombstoning
    # atomic.
    existing = _decode_json(await redis.get(f"workspace_mcp:refresh:{token_hash}"))
    family = str((existing or {}).get("family_id") or "unknown")
    result = await redis.eval(
        _CONSUME_REFRESH_SCRIPT,
        4,
        f"workspace_mcp:refresh:{token_hash}",
        f"workspace_mcp:refresh_used:{token_hash}",
        f"workspace_mcp:refresh_family:{family}",
        f"workspace_mcp:refresh_family_revoked:{family}",
        token_hash,
    )
    status = result[0].decode() if isinstance(result[0], bytes) else str(result[0])
    raw = result[1]
    if status == "consumed":
        return status, _decode_json(raw)
    value = raw.decode() if isinstance(raw, bytes) else str(raw or "")
    return status, value or None


async def revoke_refresh_family(request: Request, family_id: str) -> None:
    redis = _redis(request)
    ttl = settings.WORKSPACE_MCP_REFRESH_TOKEN_DAYS * 86400
    await redis.eval(
        _REVOKE_REFRESH_FAMILY_SCRIPT,
        2,
        f"workspace_mcp:refresh_family_revoked:{family_id}",
        f"workspace_mcp:refresh_family:{family_id}",
        ttl,
        "workspace_mcp:refresh:",
    )


async def revoke_grant_refresh_tokens(
    request: Request, grant_id: uuid.UUID | str
) -> None:
    redis = _redis(request)
    key = f"workspace_mcp:grant_refresh_families:{grant_id}"
    families = await redis.smembers(key)
    for value in families:
        family = value.decode() if isinstance(value, bytes) else str(value)
        await revoke_refresh_family(request, family)
    await redis.delete(key)


def consent_sha256(*, client_id: str, scopes: frozenset[str]) -> str:
    payload = {
        "client_id": client_id,
        "consent_version": CONSENT_VERSION,
        "notice": CONSENT_NOTICE,
        "scopes": sorted(scopes),
    }
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


async def replace_active_grant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    client: WorkspaceMCPClient,
    scopes: frozenset[str],
) -> WorkspaceMCPGrant:
    now = datetime.now(timezone.utc)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": f"workspace-mcp-grant:{tenant_id}:{user_id}:{client.client_id}"},
    )
    previous = await db.scalar(
        select(WorkspaceMCPGrant)
        .where(
            WorkspaceMCPGrant.tenant_id == tenant_id,
            WorkspaceMCPGrant.user_id == user_id,
            WorkspaceMCPGrant.client_id == client.client_id,
            WorkspaceMCPGrant.status == "active",
            WorkspaceMCPGrant.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if previous is not None:
        previous.status = "revoked"
        previous.revoked_at = now
        previous.revoked_by_user_id = user_id
        previous.revocation_reason = "Replaced by a new consent grant"
    grant = WorkspaceMCPGrant(
        tenant_id=tenant_id,
        user_id=user_id,
        client_id=client.client_id,
        client_name=client.client_name,
        scopes=sorted(scopes),
        consent_version=CONSENT_VERSION,
        consent_sha256=consent_sha256(client_id=client.client_id, scopes=scopes),
        expires_at=now + timedelta(days=settings.WORKSPACE_MCP_GRANT_DAYS),
    )
    db.add(grant)
    await db.flush()
    return grant


def _bounded_audit_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    blocked = ("token", "secret", "authorization", "verifier", "code")
    result: dict[str, Any] = {}
    for raw_key, raw_value in (metadata or {}).items():
        key = str(raw_key)[:80]
        if not key or any(part in key.casefold() for part in blocked):
            continue
        if isinstance(raw_value, (bool, int, float)) or raw_value is None:
            result[key] = raw_value
        elif isinstance(raw_value, (list, tuple)):
            result[key] = [str(value)[:200] for value in raw_value[:25]]
        else:
            result[key] = str(raw_value)[:500]
    encoded = _json_bytes(result)
    if len(encoded) > 8000:
        raise ValueError("Workspace MCP audit metadata is too large")
    return result


async def append_workspace_mcp_audit(
    db: AsyncSession,
    request: Request,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    grant_id: uuid.UUID | None,
    client_id: str,
    event_type: str,
    outcome: str,
    tool_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkspaceMCPAuditEvent:
    clean_metadata = _bounded_audit_metadata(metadata)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": f"workspace-mcp-audit:{tenant_id}"},
    )
    previous = await db.scalar(
        select(WorkspaceMCPAuditEvent)
        .where(WorkspaceMCPAuditEvent.tenant_id == tenant_id)
        .order_by(WorkspaceMCPAuditEvent.chain_position.desc())
        .limit(1)
    )
    position = previous.chain_position + 1 if previous else 1
    previous_hash = previous.event_hash if previous else None
    event_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    request_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or request.headers.get("X-Idempotency-Key")
    )
    client_id_value = client_id[:200]
    event_type_value = event_type[:80]
    tool_name_value = tool_name[:120] if tool_name else None
    request_id_value = str(request_id)[:200] if request_id else None
    ip_address_value = (_client_ip(request) or "")[:45] or None
    user_agent_value = (request.headers.get("user-agent") or "")[:500] or None
    payload = {
        "id": str(event_id),
        "tenant_id": str(tenant_id),
        "user_id": str(user_id) if user_id else None,
        "grant_id": str(grant_id) if grant_id else None,
        "client_id": client_id_value,
        "event_type": event_type_value,
        "tool_name": tool_name_value,
        "outcome": outcome,
        "request_id": request_id_value,
        "ip_address": ip_address_value,
        "user_agent": user_agent_value,
        "metadata": clean_metadata,
        "chain_position": position,
        "prev_event_hash": previous_hash,
        "created_at": created_at.isoformat(),
    }
    event = WorkspaceMCPAuditEvent(
        id=event_id,
        tenant_id=tenant_id,
        user_id=user_id,
        grant_id=grant_id,
        client_id=client_id_value,
        event_type=event_type_value,
        tool_name=tool_name_value,
        outcome=outcome,
        request_id=request_id_value,
        ip_address=ip_address_value,
        user_agent=user_agent_value,
        metadata_json=clean_metadata,
        chain_position=position,
        prev_event_hash=previous_hash,
        event_hash=hashlib.sha256(_json_bytes(payload)).hexdigest(),
        created_at=created_at,
    )
    db.add(event)
    await db.flush()
    return event
