"""Standards-based OAuth surface and self-service grants for workspace MCP."""

from __future__ import annotations

import secrets
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.user import User
from app.models.workspace_mcp_audit import WorkspaceMCPAuditEvent
from app.models.workspace_mcp_client import WorkspaceMCPClient
from app.models.workspace_mcp_grant import WorkspaceMCPGrant
from app.services.rbac_service import get_user_capabilities
from app.services.tenant_state import require_active_tenant
from app.services.workspace_mcp_grants import (
    WorkspaceMCPGrantError,
    require_active_workspace_grant,
)
from app.services.workspace_mcp_oauth import (
    CONSENT_NOTICE,
    WORKSPACE_SCOPE_LABELS,
    WorkspaceOAuthError,
    claim_authorization_request,
    append_workspace_mcp_audit,
    consume_authorization_code,
    delete_authorization_code,
    finalize_authorization_request,
    consume_refresh_token,
    issue_refresh_token,
    load_authorization_request,
    load_authorization_code,
    mint_workspace_access_token,
    normalized_scopes,
    replace_active_grant,
    require_workspace_tenant_allowed,
    revoke_grant_refresh_tokens,
    revoke_refresh_family,
    save_authorization_code,
    restore_authorization_request,
    save_authorization_request,
    validate_pkce_challenge,
    validate_redirect_uri,
    verify_pkce,
    workspace_issuer_uri,
    workspace_jwks,
    workspace_resource_is_allowed,
    workspace_resource_uri,
)

settings = get_settings()
router = APIRouter(tags=["workspace-mcp-oauth"])
logger = logging.getLogger(__name__)

_SCOPE_APP_CAPABILITIES: dict[str, frozenset[str]] = {
    "matters:read": frozenset({"manage_matters"}),
    "tasks:read": frozenset({"manage_matters"}),
    "contacts:read": frozenset({"manage_matters"}),
    "documents:read": frozenset({"manage_matters", "manage_documents"}),
    "templates:read": frozenset({"manage_matters", "manage_documents"}),
    "tasks:propose": frozenset({"manage_matters"}),
    "communications:propose": frozenset({"manage_matters"}),
    "documents:propose": frozenset({"manage_matters", "manage_documents"}),
}


class ConsentDecision(BaseModel):
    approved: bool


class RevokeGrantRequest(BaseModel):
    reason: str = "Disconnected by user"


def _require_enabled() -> None:
    if not settings.WORKSPACE_MCP_ENABLED:
        raise HTTPException(status_code=404, detail="Workspace MCP is disabled")


def _oauth_error(exc: WorkspaceOAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "error_description": exc.description},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _append_redirect_query(url: str, **values: str | None) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((key, value) for key, value in values.items() if value is not None)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _scope_items(scopes: frozenset[str]) -> list[dict[str, str]]:
    return [
        {"name": scope, "label": WORKSPACE_SCOPE_LABELS[scope]}
        for scope in sorted(scopes)
    ]


async def _cleanup_failed_refresh_exchange(
    request: Request, family_id: str | None
) -> None:
    if not family_id:
        return
    try:
        await revoke_refresh_family(request, family_id)
    except Exception:
        logger.exception("Workspace MCP failed-exchange cleanup was incomplete")


async def _allowed_user_scopes(db: AsyncSession, user: User) -> frozenset[str]:
    capabilities = frozenset(await get_user_capabilities(db, user.id))
    return frozenset(
        scope
        for scope, required in _SCOPE_APP_CAPABILITIES.items()
        if required.issubset(capabilities)
    )


async def _active_client(db: AsyncSession, client_id: str) -> WorkspaceMCPClient:
    client = await db.scalar(
        select(WorkspaceMCPClient).where(WorkspaceMCPClient.client_id == client_id)
    )
    if client is None or not client.is_active():
        raise WorkspaceOAuthError("invalid_client", "OAuth client is unavailable")
    return client


async def _load_grant_actor(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    grant_id: str,
    client_id: str,
    scopes: frozenset[str],
) -> tuple[WorkspaceMCPGrant, User]:
    require_workspace_tenant_allowed(tenant_id)
    await set_tenant_context(db, str(tenant_id))
    try:
        grant = await require_active_workspace_grant(
            db,
            grant_id=grant_id,
            tenant_id=tenant_id,
            user_id=user_id,
            client_id=client_id,
            token_scopes=scopes,
        )
    except WorkspaceMCPGrantError as exc:
        raise WorkspaceOAuthError(
            "invalid_grant", "Workspace consent grant is unavailable"
        ) from exc
    user = await db.scalar(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    if user is None or not user.is_active or not user.license_active:
        raise WorkspaceOAuthError("invalid_grant", "Workspace user is unavailable")
    if not getattr(user, "workspace_mcp_enabled", True):
        raise WorkspaceOAuthError(
            "invalid_grant",
            "Workspace MCP access is disabled for this user by the tenant administrator",
        )
    if user.privacy_mode:
        raise WorkspaceOAuthError(
            "invalid_grant", "Workspace MCP is unavailable in Privacy Mode"
        )
    require_active_tenant(user.tenant)
    allowed = await _allowed_user_scopes(db, user)
    if not scopes.issubset(allowed):
        raise WorkspaceOAuthError(
            "invalid_scope", "Current LawHand permissions no longer allow this scope"
        )
    return grant, user


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/api/mcp/workspace")
async def protected_resource_metadata(request: Request = None):
    research_host = urlsplit(settings.research_mcp_endpoint).hostname
    if request is not None and request.url.hostname == research_host:
        from app.routers.research_mcp_oauth import (
            _require_enabled as require_research_enabled,
            protected_resource_metadata_payload,
        )

        require_research_enabled()
        return protected_resource_metadata_payload()
    _require_enabled()
    return {
        "resource": workspace_resource_uri(),
        "resource_name": "LawHand Workspace MCP",
        "authorization_servers": [workspace_issuer_uri()],
        "scopes_supported": sorted(WORKSPACE_SCOPE_LABELS),
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request = None):
    research_host = urlsplit(settings.research_mcp_endpoint).hostname
    if request is not None and request.url.hostname == research_host:
        from app.routers.research_mcp_oauth import (
            _require_enabled as require_research_enabled,
            authorization_server_metadata_payload,
        )

        require_research_enabled()
        return authorization_server_metadata_payload()
    _require_enabled()
    issuer = workspace_issuer_uri()
    metadata = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/api/workspace-mcp/oauth/authorize",
        "token_endpoint": f"{issuer}/api/workspace-mcp/oauth/token",
        "revocation_endpoint": f"{issuer}/api/workspace-mcp/oauth/revoke",
        "jwks_uri": f"{issuer}/api/workspace-mcp/oauth/jwks",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": sorted(WORKSPACE_SCOPE_LABELS),
        "client_id_metadata_document_supported": False,
    }
    if settings.WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED:
        metadata["registration_endpoint"] = f"{issuer}/api/workspace-mcp/oauth/register"
    return metadata


@router.get("/api/workspace-mcp/oauth/jwks")
async def workspace_jwks_endpoint():
    _require_enabled()
    return workspace_jwks()


@router.post("/api/workspace-mcp/oauth/register", status_code=201)
async def register_workspace_client(
    request: Request, db: AsyncSession = Depends(get_db)
):
    _require_enabled()
    if not settings.WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED:
        return _oauth_error(
            WorkspaceOAuthError(
                "invalid_client_metadata",
                "Dynamic registration is unavailable",
                status_code=403,
            )
        )
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise WorkspaceOAuthError(
                "invalid_client_metadata", "Registration metadata must be an object"
            )
        client_name = str(payload.get("client_name") or "").strip()
        if not 1 <= len(client_name) <= 200:
            raise WorkspaceOAuthError(
                "invalid_client_metadata", "client_name is required"
            )
        redirect_values = payload.get("redirect_uris")
        if not isinstance(redirect_values, list) or not 1 <= len(redirect_values) <= 10:
            raise WorkspaceOAuthError(
                "invalid_redirect_uri", "One to ten redirect_uris are required"
            )
        redirect_uris = [validate_redirect_uri(value) for value in redirect_values]
        if len(set(redirect_uris)) != len(redirect_uris):
            raise WorkspaceOAuthError(
                "invalid_client_metadata", "redirect_uris must be unique"
            )
        grant_types = payload.get(
            "grant_types", ["authorization_code", "refresh_token"]
        )
        response_types = payload.get("response_types", ["code"])
        if (
            not isinstance(grant_types, list)
            or "authorization_code" not in grant_types
            or set(grant_types) - {"authorization_code", "refresh_token"}
            or response_types != ["code"]
            or payload.get("token_endpoint_auth_method", "none") != "none"
        ):
            raise WorkspaceOAuthError(
                "invalid_client_metadata",
                "Only public authorization-code clients with S256 PKCE are supported",
            )
        now = datetime.now(timezone.utc)
        client = WorkspaceMCPClient(
            client_id="lhmcp_" + secrets.token_urlsafe(32),
            client_name=client_name,
            redirect_uris=redirect_uris,
            grant_types=grant_types,
            response_types=["code"],
            token_endpoint_auth_method="none",
            software_id=str(payload.get("software_id") or "")[:200] or None,
            software_version=str(payload.get("software_version") or "")[:100] or None,
            expires_at=now
            + timedelta(days=settings.WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS),
        )
        db.add(client)
        await db.commit()
        logger.info(
            "Workspace MCP public client registered client_id=%s software_id=%s request_id=%s",
            client.client_id,
            client.software_id or "-",
            getattr(request.state, "request_id", None) or "-",
        )
        return JSONResponse(
            status_code=201,
            content={
                "client_id": client.client_id,
                "client_id_issued_at": int(client.created_at.timestamp()),
                "client_id_expires_at": int(client.expires_at.timestamp()),
                "client_name": client.client_name,
                "redirect_uris": client.redirect_uris,
                "grant_types": client.grant_types,
                "response_types": client.response_types,
                "token_endpoint_auth_method": "none",
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except WorkspaceOAuthError as exc:
        await db.rollback()
        return _oauth_error(exc)
    except Exception:
        await db.rollback()
        raise


@router.get("/api/workspace-mcp/oauth/authorize")
async def begin_workspace_authorization(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(..., min_length=1, max_length=200),
    redirect_uri: str = Query(..., min_length=1, max_length=1000),
    scope: str = Query(..., min_length=1, max_length=500),
    state: str = Query("", max_length=1000),
    code_challenge: str = Query(..., min_length=43, max_length=128),
    code_challenge_method: str = Query(...),
    resource: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    try:
        client = await _active_client(db, client_id)
        if response_type != "code":
            raise WorkspaceOAuthError(
                "unsupported_response_type", "Only authorization code is supported"
            )
        if redirect_uri not in client.redirect_uri_set:
            raise WorkspaceOAuthError("invalid_request", "Redirect URI does not match")
        if not workspace_resource_is_allowed(resource):
            raise WorkspaceOAuthError("invalid_target", "OAuth resource is invalid")
        scopes = normalized_scopes(scope)
        validate_pkce_challenge(code_challenge, code_challenge_method)
        request_id = await save_authorization_request(
            request,
            {
                "client_id": client.client_id,
                "redirect_uri": redirect_uri,
                "scopes": sorted(scopes),
                "state": state,
                "code_challenge": code_challenge,
                "resource": workspace_resource_uri(),
                "created_at": int(time.time()),
            },
        )
        return RedirectResponse(
            _append_redirect_query(
                f"{settings.FRONTEND_URL.rstrip('/')}/workspace-mcp/authorize",
                request_id=request_id,
            ),
            status_code=302,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except WorkspaceOAuthError as exc:
        return _oauth_error(exc)


@router.get("/api/workspace-mcp/oauth/requests/{request_id}")
async def get_workspace_authorization_request(
    request_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    require_workspace_tenant_allowed(user.tenant_id)
    pending = await load_authorization_request(request, request_id)
    if pending is None:
        raise HTTPException(
            status_code=410, detail="Authorization request expired or was already used"
        )
    client = await _active_client(db, str(pending.get("client_id") or ""))
    scopes = frozenset(str(value) for value in pending.get("scopes", []))
    allowed = await _allowed_user_scopes(db, user)
    if not scopes or not scopes.issubset(allowed):
        raise HTTPException(
            status_code=403,
            detail="Your current LawHand permissions do not allow the requested access",
        )
    return {
        "request_id": request_id,
        "client": {"id": client.client_id, "name": client.client_name},
        "organization": {"id": str(user.tenant_id), "name": user.tenant.name},
        "user": {"id": str(user.id), "email": user.email, "name": user.full_name},
        "scopes": _scope_items(scopes),
        "notice": CONSENT_NOTICE,
        "expires_in": settings.WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS,
    }


@router.post("/api/workspace-mcp/oauth/requests/{request_id}/decision")
async def decide_workspace_authorization(
    request_id: str,
    decision: ConsentDecision,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    require_workspace_tenant_allowed(user.tenant_id)
    pending = await claim_authorization_request(request, request_id)
    if pending is None:
        raise HTTPException(
            status_code=410, detail="Authorization request expired or is already in use"
        )

    code = ""
    committed = False
    try:
        client = await _active_client(db, str(pending.get("client_id") or ""))
        redirect_uri = str(pending.get("redirect_uri") or "")
        if redirect_uri not in client.redirect_uri_set:
            raise HTTPException(
                status_code=400, detail="OAuth redirect no longer matches"
            )
        # Do this before creating a grant or authorization code.  Consent is
        # not useful when the user's privacy policy prevents the resulting
        # client from receiving a token or reaching the workspace transport.
        # Previously this was checked only during token exchange/runtime,
        # leaving an apparently active but unusable assistant in the portal.
        if not getattr(user, "workspace_mcp_enabled", True):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Workspace MCP access is disabled for your account. "
                    "Ask a tenant administrator to enable it in Admin > Users."
                ),
            )
        if user.privacy_mode:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Workspace MCP is paused because Privacy Mode is enabled in "
                    "your LawHand profile. Privacy Mode redacts private details and "
                    "disconnects external assistants; turn it off to reconnect."
                ),
            )
        scopes = frozenset(str(value) for value in pending.get("scopes", []))
        if not scopes or not scopes.issubset(await _allowed_user_scopes(db, user)):
            raise HTTPException(
                status_code=403, detail="Requested access is unavailable"
            )

        if not decision.approved:
            await append_workspace_mcp_audit(
                db,
                request,
                tenant_id=user.tenant_id,
                user_id=user.id,
                grant_id=None,
                client_id=client.client_id,
                event_type="consent_denied",
                outcome="denied",
                metadata={"scopes": sorted(scopes)},
            )
            await db.commit()
            committed = True
            try:
                await finalize_authorization_request(request, request_id)
            except Exception:
                logger.exception(
                    "Workspace MCP consent-denial cleanup failed request_id=%s",
                    request_id,
                )
            return {
                "redirect_to": _append_redirect_query(
                    redirect_uri,
                    error="access_denied",
                    error_description="The user declined LawHand workspace access",
                    state=str(pending.get("state") or "") or None,
                )
            }

        previous = await db.scalar(
            select(WorkspaceMCPGrant).where(
                WorkspaceMCPGrant.tenant_id == user.tenant_id,
                WorkspaceMCPGrant.user_id == user.id,
                WorkspaceMCPGrant.client_id == client.client_id,
                WorkspaceMCPGrant.status == "active",
                WorkspaceMCPGrant.revoked_at.is_(None),
            )
        )
        grant = await replace_active_grant(
            db,
            tenant_id=user.tenant_id,
            user_id=user.id,
            client=client,
            scopes=scopes,
        )
        code = await save_authorization_code(
            request,
            {
                "tenant_id": str(user.tenant_id),
                "user_id": str(user.id),
                "grant_id": str(grant.id),
                "client_id": client.client_id,
                "redirect_uri": redirect_uri,
                "scopes": sorted(scopes),
                "code_challenge": str(pending.get("code_challenge") or ""),
                "resource": workspace_resource_uri(),
            },
        )
        await append_workspace_mcp_audit(
            db,
            request,
            tenant_id=user.tenant_id,
            user_id=user.id,
            grant_id=grant.id,
            client_id=client.client_id,
            event_type="consent_granted",
            outcome="success",
            metadata={"scopes": sorted(scopes), "replaced_grant": bool(previous)},
        )
        await db.commit()
        committed = True
        try:
            await finalize_authorization_request(request, request_id)
            if previous is not None:
                await revoke_grant_refresh_tokens(request, previous.id)
        except Exception:
            # The database grant is authoritative, so any older grant is
            # rejected even if its Redis refresh-family cleanup is delayed.
            logger.exception(
                "Workspace MCP post-consent cleanup failed request_id=%s",
                request_id,
            )
        return {
            "redirect_to": _append_redirect_query(
                redirect_uri,
                code=code,
                state=str(pending.get("state") or "") or None,
            )
        }
    except Exception as exc:
        if not committed:
            await db.rollback()
            if code:
                try:
                    await delete_authorization_code(request, code)
                except Exception:
                    logger.exception(
                        "Workspace MCP failed to remove an uncommitted code"
                    )
            try:
                await restore_authorization_request(request, request_id)
            except Exception:
                logger.exception(
                    "Workspace MCP failed to restore consent request request_id=%s",
                    request_id,
                )
        if isinstance(exc, WorkspaceOAuthError):
            raise HTTPException(
                status_code=exc.status_code, detail=exc.description
            ) from exc
        raise


@router.post("/api/workspace-mcp/oauth/token")
async def workspace_token(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    cleanup_family_id: str | None = None
    try:
        form = await request.form()
        grant_type = str(form.get("grant_type") or "")
        client_id = str(form.get("client_id") or "")
        client = await _active_client(db, client_id)
        resource = str(form.get("resource") or "")
        if not workspace_resource_is_allowed(resource):
            raise WorkspaceOAuthError("invalid_target", "OAuth resource is invalid")

        if grant_type == "authorization_code":
            code = str(form.get("code") or "")
            payload = await load_authorization_code(request, code)
            if payload is None:
                raise WorkspaceOAuthError(
                    "invalid_grant", "Authorization code is invalid or expired"
                )
            redirect_uri = str(form.get("redirect_uri") or "")
            verifier = str(form.get("code_verifier") or "")
            if (
                payload.get("client_id") != client.client_id
                or payload.get("redirect_uri") != redirect_uri
                or not workspace_resource_is_allowed(payload.get("resource"))
                or not verify_pkce(verifier, str(payload.get("code_challenge") or ""))
            ):
                raise WorkspaceOAuthError(
                    "invalid_grant", "Authorization code binding is invalid"
                )
            tenant_id = uuid.UUID(str(payload["tenant_id"]))
            user_id = uuid.UUID(str(payload["user_id"]))
            scopes = frozenset(str(value) for value in payload["scopes"])
            grant, user = await _load_grant_actor(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                grant_id=str(payload["grant_id"]),
                client_id=client.client_id,
                scopes=scopes,
            )
            if not await consume_authorization_code(request, code, payload):
                raise WorkspaceOAuthError(
                    "invalid_grant", "Authorization code was already used"
                )

            event_type = "authorization_code_exchanged"
            family_id = str(uuid.uuid4())
        elif grant_type == "refresh_token":
            refresh_value = str(form.get("refresh_token") or "")
            status, consumed = await consume_refresh_token(request, refresh_value)
            if status == "replay" and isinstance(consumed, str):
                await revoke_refresh_family(request, consumed)
                raise WorkspaceOAuthError(
                    "invalid_grant", "Refresh token reuse was detected"
                )
            if status != "consumed" or not isinstance(consumed, dict):
                raise WorkspaceOAuthError(
                    "invalid_grant", "Refresh token is invalid or expired"
                )
            family_id = str(consumed.get("family_id") or "")
            if not family_id:
                raise WorkspaceOAuthError(
                    "invalid_grant", "Refresh token family is invalid"
                )
            cleanup_family_id = family_id
            if consumed.get(
                "client_id"
            ) != client.client_id or not workspace_resource_is_allowed(
                consumed.get("resource")
            ):
                raise WorkspaceOAuthError(
                    "invalid_grant", "Refresh token binding is invalid"
                )
            tenant_id = uuid.UUID(str(consumed["tenant_id"]))
            user_id = uuid.UUID(str(consumed["user_id"]))
            scopes = frozenset(str(value) for value in consumed["scopes"])
            grant, user = await _load_grant_actor(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                grant_id=str(consumed["grant_id"]),
                client_id=client.client_id,
                scopes=scopes,
            )
            event_type = "refresh_token_rotated"
        else:
            raise WorkspaceOAuthError(
                "unsupported_grant_type", "Unsupported OAuth grant type"
            )

        access_token, _token_id, expires_in = mint_workspace_access_token(
            user_id=user.id,
            tenant_id=tenant_id,
            client_id=client.client_id,
            grant_id=grant.id,
            scopes=scopes,
        )
        refresh_token = await issue_refresh_token(
            request,
            user_id=user.id,
            tenant_id=tenant_id,
            client_id=client.client_id,
            grant_id=grant.id,
            scopes=scopes,
            family_id=family_id,
        )
        cleanup_family_id = family_id
        client.last_used_at = datetime.now(timezone.utc)
        await append_workspace_mcp_audit(
            db,
            request,
            tenant_id=tenant_id,
            user_id=user.id,
            grant_id=grant.id,
            client_id=client.client_id,
            event_type=event_type,
            outcome="success",
            metadata={"scopes": sorted(scopes)},
        )
        await db.commit()
        cleanup_family_id = None
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": expires_in,
                "refresh_token": refresh_token,
                "scope": " ".join(sorted(scopes)),
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except WorkspaceOAuthError as exc:
        await db.rollback()
        await _cleanup_failed_refresh_exchange(request, cleanup_family_id)
        return _oauth_error(exc)
    except (KeyError, TypeError, ValueError):
        await db.rollback()
        await _cleanup_failed_refresh_exchange(request, cleanup_family_id)
        return _oauth_error(
            WorkspaceOAuthError("invalid_grant", "OAuth token request is invalid")
        )
    except Exception:
        await db.rollback()
        await _cleanup_failed_refresh_exchange(request, cleanup_family_id)
        raise


@router.post("/api/workspace-mcp/oauth/revoke")
async def revoke_workspace_token(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    form = await request.form()
    token = str(form.get("token") or "")
    client_id = str(form.get("client_id") or "")
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return JSONResponse(
            status_code=503,
            content={"error": "temporarily_unavailable"},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    try:
        await _active_client(db, client_id)
        if token.startswith("wmr_"):
            status, consumed = await consume_refresh_token(request, token)
            if status == "consumed" and isinstance(consumed, dict):
                if consumed.get("client_id") == client_id:
                    family_id = str(consumed.get("family_id") or "")
                    if family_id:
                        await revoke_refresh_family(request, family_id)
            elif status == "replay" and isinstance(consumed, str):
                await revoke_refresh_family(request, consumed)
        else:
            from app.services.workspace_mcp_protocol import (
                decode_workspace_access_token,
            )

            identity = decode_workspace_access_token(token)
            if identity.client_id == client_id:
                await redis.setex(
                    f"jti:{identity.token_id}",
                    settings.WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES * 60,
                    b"1",
                )
    except RedisError:
        logger.exception("Workspace MCP token revocation storage failed")
        return JSONResponse(
            status_code=503,
            content={"error": "temporarily_unavailable"},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except Exception:
        # RFC 7009 deliberately does not reveal whether a token was valid.
        pass
    return JSONResponse({}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/api/workspace-mcp/grants")
async def list_workspace_grants(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    require_workspace_tenant_allowed(user.tenant_id)
    grants = list(
        (
            await db.scalars(
                select(WorkspaceMCPGrant)
                .where(
                    WorkspaceMCPGrant.tenant_id == user.tenant_id,
                    WorkspaceMCPGrant.user_id == user.id,
                )
                .order_by(WorkspaceMCPGrant.created_at.desc())
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    changed = False
    for grant in grants:
        if grant.status == "active" and grant.expires_at <= now:
            grant.status = "expired"
            changed = True
    if changed:
        await db.commit()
    return {
        "items": [
            {
                "id": str(grant.id),
                "client_id": grant.client_id,
                "client_name": grant.client_name,
                "scopes": _scope_items(grant.scope_set),
                "status": grant.status,
                "created_at": grant.created_at.isoformat(),
                "expires_at": grant.expires_at.isoformat(),
                "last_used_at": grant.last_used_at.isoformat()
                if grant.last_used_at
                else None,
                "revoked_at": grant.revoked_at.isoformat()
                if grant.revoked_at
                else None,
            }
            for grant in grants
        ]
    }


@router.post("/api/workspace-mcp/grants/{grant_id}/revoke")
async def revoke_workspace_grant(
    grant_id: uuid.UUID,
    payload: RevokeGrantRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    require_workspace_tenant_allowed(user.tenant_id)
    grant = await db.scalar(
        select(WorkspaceMCPGrant)
        .where(
            WorkspaceMCPGrant.id == grant_id,
            WorkspaceMCPGrant.tenant_id == user.tenant_id,
            WorkspaceMCPGrant.user_id == user.id,
        )
        .with_for_update()
    )
    if grant is None:
        raise HTTPException(status_code=404, detail="Workspace connection not found")
    if grant.status == "active":
        grant.status = "revoked"
        grant.revoked_at = datetime.now(timezone.utc)
        grant.revoked_by_user_id = user.id
        grant.revocation_reason = payload.reason.strip()[:500] or "Disconnected by user"
        await append_workspace_mcp_audit(
            db,
            request,
            tenant_id=user.tenant_id,
            user_id=user.id,
            grant_id=grant.id,
            client_id=grant.client_id,
            event_type="grant_revoked",
            outcome="success",
            metadata={"reason": grant.revocation_reason},
        )
        await db.commit()
        try:
            redis = getattr(request.app.state, "redis", None)
            if redis is not None:
                await redis.setex(
                    f"workspace_mcp_grant:{grant.id}",
                    settings.WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES * 60,
                    b"1",
                )
                await revoke_grant_refresh_tokens(request, grant.id)
        except Exception:
            # The committed grant is authoritative for every tool/token check.
            logger.exception("Workspace MCP post-revocation cache cleanup failed")
    return {"id": str(grant.id), "status": grant.status}


@router.get("/api/workspace-mcp/audit")
async def list_workspace_audit(
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    require_workspace_tenant_allowed(user.tenant_id)
    events = list(
        (
            await db.scalars(
                select(WorkspaceMCPAuditEvent)
                .where(
                    WorkspaceMCPAuditEvent.tenant_id == user.tenant_id,
                    WorkspaceMCPAuditEvent.user_id == user.id,
                )
                .order_by(WorkspaceMCPAuditEvent.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": str(event.id),
                "event_type": event.event_type,
                "client_id": event.client_id,
                "grant_id": str(event.grant_id) if event.grant_id else None,
                "tool_name": event.tool_name,
                "outcome": event.outcome,
                "metadata": event.metadata_json,
                "chain_position": event.chain_position,
                "event_hash": event.event_hash,
                "previous_event_hash": event.prev_event_hash,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]
    }
