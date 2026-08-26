"""OAuth 2.1 bindings for the public LawHand Research MCP resource.

The protocol primitives and signing key-ring are shared with Workspace MCP.
Issuer, audience, token type, Redis namespace and durable consent are bound to
Research so a token or refresh family can never cross the product boundary.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.workspace_mcp_client import WorkspaceMCPClient
from app.models.workspace_mcp_grant import WorkspaceMCPGrant
from app.services.workspace_mcp_grants import (
    WorkspaceMCPGrantError,
    require_active_workspace_grant,
)
from app.services.workspace_mcp_oauth import (
    OFFLINE_ACCESS_LABEL,
    OFFLINE_ACCESS_SCOPE,
    WorkspaceOAuthError,
    claim_authorization_request,
    consume_authorization_code,
    consume_refresh_token,
    delete_authorization_code,
    finalize_authorization_request,
    issue_refresh_token,
    load_authorization_code,
    load_authorization_request,
    replace_active_grant,
    restore_authorization_request,
    revoke_grant_refresh_tokens,
    revoke_refresh_family,
    save_authorization_code,
    save_authorization_request,
    sign_mcp_access_token,
    validate_pkce_challenge,
    validate_redirect_uri,
    verify_pkce,
    workspace_jwks,
    workspace_verification_key,
)

settings = get_settings()

RESEARCH_SCOPE = "research:read"
RESEARCH_SCOPE_LABELS = {
    RESEARCH_SCOPE: "Search and retrieve public legal research authorities",
}
RESEARCH_OAUTH_SCOPE_LABELS = {
    **RESEARCH_SCOPE_LABELS,
    OFFLINE_ACCESS_SCOPE: OFFLINE_ACCESS_LABEL,
}
CONSENT_VERSION = "research-mcp-v1"
CONSENT_NOTICE = (
    "LawHand Research accesses public legal authorities and records metered "
    "usage to your LawHand tenant. It cannot access matters or workspace files."
)
NAMESPACE = "research_mcp"
CLIENT_ID_PREFIX = "research."


def research_resource_uri() -> str:
    return settings.research_mcp_endpoint


def research_issuer_uri() -> str:
    return settings.RESEARCH_MCP_ISSUER.rstrip("/")


def research_protected_resource_metadata_uri() -> str:
    resource = urlsplit(research_resource_uri())
    return (
        f"{resource.scheme}://{resource.netloc}"
        f"/.well-known/oauth-protected-resource{resource.path}"
    )


def normalized_research_scopes(raw: str) -> frozenset[str]:
    scopes = frozenset(value for value in raw.split() if value)
    if RESEARCH_SCOPE not in scopes or scopes - RESEARCH_OAUTH_SCOPE_LABELS.keys():
        raise WorkspaceOAuthError("invalid_scope", "research:read is required")
    return scopes


def research_client_id(raw: str) -> str:
    """Namespace public registrations in the shared durable client registry."""

    value = raw.strip()
    return value if value.startswith(CLIENT_ID_PREFIX) else CLIENT_ID_PREFIX + value


def mint_research_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    client_id: str,
    grant_id: uuid.UUID,
    scopes: frozenset[str],
) -> tuple[str, str, int]:
    now = datetime.now(timezone.utc)
    expires_in = settings.RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES * 60
    token_id = str(uuid.uuid4())
    claims: dict[str, Any] = {
        "iss": research_issuer_uri(),
        "aud": settings.RESEARCH_MCP_AUDIENCE,
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "type": "research_mcp",
        "token_use": "access",
        "client_id": client_id,
        "grant_id": str(grant_id),
        "jti": token_id,
        "scope": " ".join(sorted(scopes)),
        "resource": research_resource_uri(),
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return sign_mcp_access_token(claims), token_id, expires_in


def decode_research_access_token(token: str) -> dict[str, Any]:
    try:
        key, algorithm = workspace_verification_key(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience=settings.RESEARCH_MCP_AUDIENCE,
            issuer=research_issuer_uri(),
            options={
                "require_aud": True,
                "require_exp": True,
                "require_iat": True,
                "require_iss": True,
                "require_jti": True,
                "require_sub": True,
            },
        )
    except (JWTError, WorkspaceOAuthError, TypeError, ValueError) as exc:
        raise WorkspaceOAuthError(
            "invalid_token", "Research access token is invalid"
        ) from exc
    required = {
        "sub",
        "tenant_id",
        "client_id",
        "grant_id",
        "jti",
        "scope",
        "resource",
    }
    if (
        claims.get("type") != "research_mcp"
        or claims.get("token_use") != "access"
        or claims.get("resource") != research_resource_uri()
        or not required.issubset(claims)
        or frozenset(str(claims["scope"]).split()) != frozenset({RESEARCH_SCOPE})
    ):
        raise WorkspaceOAuthError("invalid_token", "Research token binding is invalid")

    if any(
        not str(claims.get(name) or "").strip() or len(str(claims[name])) > 200
        for name in ("sub", "tenant_id", "client_id", "grant_id", "jti")
    ):
        raise WorkspaceOAuthError("invalid_token", "Research token binding is invalid")
    try:
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceOAuthError(
            "invalid_token", "Research token lifetime is invalid"
        ) from exc
    now = int(time.time())
    maximum_lifetime = settings.RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES * 60
    if (
        issued_at > now + 60
        or expires_at <= issued_at
        or expires_at - issued_at > maximum_lifetime
    ):
        raise WorkspaceOAuthError("invalid_token", "Research token lifetime is invalid")
    return claims


async def save_research_authorization_request(
    request: Request, payload: dict[str, Any]
) -> str:
    return await save_authorization_request(
        request,
        payload,
        namespace=NAMESPACE,
        ttl_seconds=settings.RESEARCH_MCP_AUTH_CODE_TTL_SECONDS,
    )


async def load_research_authorization_request(
    request: Request, request_id: str
) -> dict[str, Any] | None:
    return await load_authorization_request(request, request_id, namespace=NAMESPACE)


async def claim_research_authorization_request(
    request: Request, request_id: str
) -> dict[str, Any] | None:
    return await claim_authorization_request(request, request_id, namespace=NAMESPACE)


async def restore_research_authorization_request(
    request: Request, request_id: str
) -> None:
    await restore_authorization_request(request, request_id, namespace=NAMESPACE)


async def finalize_research_authorization_request(
    request: Request, request_id: str
) -> None:
    await finalize_authorization_request(request, request_id, namespace=NAMESPACE)


async def save_research_authorization_code(
    request: Request, payload: dict[str, Any]
) -> str:
    return await save_authorization_code(
        request,
        payload,
        namespace=NAMESPACE,
        ttl_seconds=settings.RESEARCH_MCP_AUTH_CODE_TTL_SECONDS,
    )


async def load_research_authorization_code(
    request: Request, code: str
) -> dict[str, Any] | None:
    return await load_authorization_code(request, code, namespace=NAMESPACE)


async def consume_research_authorization_code(
    request: Request, code: str, payload: dict[str, Any]
) -> bool:
    return await consume_authorization_code(request, code, payload, namespace=NAMESPACE)


async def delete_research_authorization_code(request: Request, code: str) -> None:
    await delete_authorization_code(request, code, namespace=NAMESPACE)


async def issue_research_refresh_token(
    request: Request,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    client_id: str,
    grant_id: uuid.UUID,
    scopes: frozenset[str],
    family_id: str | None = None,
) -> str:
    return await issue_refresh_token(
        request,
        user_id=user_id,
        tenant_id=tenant_id,
        client_id=client_id,
        grant_id=grant_id,
        scopes=scopes,
        family_id=family_id,
        namespace=NAMESPACE,
        token_prefix="rmr_",
        resource=research_resource_uri(),
        ttl_days=settings.RESEARCH_MCP_REFRESH_TOKEN_DAYS,
    )


async def consume_research_refresh_token(
    request: Request,
    token: str,
    *,
    expected_client_id: str | None = None,
    expected_resource: str | None = None,
):
    return await consume_refresh_token(
        request,
        token,
        namespace=NAMESPACE,
        expected_client_id=expected_client_id,
        expected_resource=expected_resource,
    )


async def revoke_research_refresh_family(request: Request, family_id: str) -> None:
    await revoke_refresh_family(
        request,
        family_id,
        namespace=NAMESPACE,
        ttl_days=settings.RESEARCH_MCP_REFRESH_TOKEN_DAYS,
    )


async def revoke_research_grant_refresh_tokens(
    request: Request, grant_id: uuid.UUID | str
) -> None:
    await revoke_grant_refresh_tokens(
        request,
        grant_id,
        namespace=NAMESPACE,
        ttl_days=settings.RESEARCH_MCP_REFRESH_TOKEN_DAYS,
    )


async def replace_active_research_grant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    client: WorkspaceMCPClient,
) -> WorkspaceMCPGrant:
    return await replace_active_grant(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        client=client,
        scopes=frozenset({RESEARCH_SCOPE}),
        consent_version=CONSENT_VERSION,
        consent_notice=CONSENT_NOTICE,
        grant_days=settings.RESEARCH_MCP_GRANT_DAYS,
    )


async def require_active_research_grant(
    db: AsyncSession,
    *,
    grant_id: str,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    client_id: str,
    scopes: frozenset[str],
) -> WorkspaceMCPGrant:
    if not client_id.startswith(CLIENT_ID_PREFIX) or scopes != frozenset(
        {RESEARCH_SCOPE}
    ):
        raise WorkspaceMCPGrantError("Research consent binding is invalid")
    grant = await require_active_workspace_grant(
        db,
        grant_id=grant_id,
        tenant_id=tenant_id,
        user_id=user_id,
        client_id=client_id,
        token_scopes=scopes,
    )
    if grant.consent_version != CONSENT_VERSION:
        raise WorkspaceMCPGrantError("Research consent grant is unavailable")
    return grant


__all__ = [
    "CONSENT_NOTICE",
    "RESEARCH_OAUTH_SCOPE_LABELS",
    "RESEARCH_SCOPE",
    "RESEARCH_SCOPE_LABELS",
    "WorkspaceOAuthError",
    "decode_research_access_token",
    "mint_research_access_token",
    "normalized_research_scopes",
    "research_issuer_uri",
    "research_protected_resource_metadata_uri",
    "research_resource_uri",
    "validate_pkce_challenge",
    "validate_redirect_uri",
    "verify_pkce",
    "workspace_jwks",
]
