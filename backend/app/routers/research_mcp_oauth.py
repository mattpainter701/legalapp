"""OAuth 2.1 authorization server for interactive Research MCP clients."""

from __future__ import annotations

import logging
import secrets
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
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.user import User
from app.models.workspace_mcp_client import WorkspaceMCPClient
from app.models.workspace_mcp_grant import WorkspaceMCPGrant
from app.services.mcp_product import ensure_mcp_product_access
from app.services.research_mcp_oauth import (
    CONSENT_NOTICE,
    RESEARCH_OAUTH_SCOPE_LABELS,
    RESEARCH_SCOPE,
    RESEARCH_SCOPE_LABELS,
    WorkspaceOAuthError,
    claim_research_authorization_request,
    consume_research_authorization_code,
    consume_research_refresh_token,
    decode_research_access_token,
    delete_research_authorization_code,
    finalize_research_authorization_request,
    issue_research_refresh_token,
    load_research_authorization_code,
    load_research_authorization_request,
    mint_research_access_token,
    normalized_research_scopes,
    replace_active_research_grant,
    research_issuer_uri,
    research_resource_uri,
    restore_research_authorization_request,
    revoke_research_grant_refresh_tokens,
    revoke_research_refresh_family,
    save_research_authorization_code,
    save_research_authorization_request,
    validate_pkce_challenge,
    validate_redirect_uri,
    verify_pkce,
    workspace_jwks,
)
from app.services.research_mcp_oauth import require_active_research_grant
from app.services.tenant_state import require_active_tenant
from app.services.workspace_mcp_oauth import (
    parse_dynamic_client_registration_payload,
)

settings = get_settings()
router = APIRouter(tags=["research-mcp-oauth"])
logger = logging.getLogger(__name__)


class ConsentDecision(BaseModel):
    approved: bool


class RevokeGrantRequest(BaseModel):
    reason: str = "Disconnected by user"


def _require_enabled() -> None:
    if not settings.MCP_PRODUCT_ENABLED or not settings.RESEARCH_MCP_OAUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Research MCP OAuth is disabled")


def _oauth_error(exc: WorkspaceOAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "error_description": exc.description},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _redirect(url: str, **values: str | None) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((key, value) for key, value in values.items() if value is not None)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


async def _active_client(db: AsyncSession, client_id: str) -> WorkspaceMCPClient:
    client = await db.scalar(
        select(WorkspaceMCPClient).where(
            WorkspaceMCPClient.client_id == client_id,
            WorkspaceMCPClient.client_id.like("research.%"),
        )
    )
    if client is None or not client.is_active():
        raise WorkspaceOAuthError("invalid_client", "OAuth client is unavailable")
    return client


async def _load_actor(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    grant_id: str,
    client_id: str,
    scopes: frozenset[str],
) -> tuple[WorkspaceMCPGrant, User]:
    await set_tenant_context(db, str(tenant_id))
    try:
        grant = await require_active_research_grant(
            db,
            grant_id=grant_id,
            tenant_id=tenant_id,
            user_id=user_id,
            client_id=client_id,
            scopes=scopes,
        )
    except Exception as exc:
        raise WorkspaceOAuthError(
            "invalid_grant", "Research consent grant is unavailable"
        ) from exc
    user = await db.scalar(
        select(User)
        .options(selectinload(User.tenant))
        .where(User.id == user_id, User.tenant_id == tenant_id)
    )
    if user is None or not user.is_active:
        raise WorkspaceOAuthError("invalid_grant", "Research user is unavailable")
    require_active_tenant(user.tenant)
    try:
        ensure_mcp_product_access(user.tenant)
    except HTTPException as exc:
        raise WorkspaceOAuthError(
            "invalid_grant", "Research product access is unavailable"
        ) from exc
    return grant, user


def protected_resource_metadata_payload() -> dict:
    return {
        "resource": research_resource_uri(),
        "resource_name": "LawHand Research MCP",
        "authorization_servers": [research_issuer_uri()],
        "scopes_supported": sorted(RESEARCH_OAUTH_SCOPE_LABELS),
        "bearer_methods_supported": ["header"],
    }


def authorization_server_metadata_payload() -> dict:
    issuer = research_issuer_uri()
    payload = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/api/research-mcp/oauth/authorize",
        "token_endpoint": f"{issuer}/api/research-mcp/oauth/token",
        "revocation_endpoint": f"{issuer}/api/research-mcp/oauth/revoke",
        "jwks_uri": f"{issuer}/api/research-mcp/oauth/jwks",
        "registration_endpoint": f"{issuer}/api/research-mcp/oauth/register",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": sorted(RESEARCH_OAUTH_SCOPE_LABELS),
        "client_id_metadata_document_supported": False,
    }
    if not settings.RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED:
        payload.pop("registration_endpoint")
    return payload


# Nginx maps the standards-defined root discovery URLs on the dedicated
# research host to these unambiguous internal routes. The path-specific
# protected-resource document does not conflict with Workspace MCP.
@router.get("/.well-known/oauth-protected-resource/api/mcp")
@router.get("/api/research-mcp/oauth/protected-resource-metadata")
async def research_protected_resource_metadata():
    _require_enabled()
    return protected_resource_metadata_payload()


@router.get("/api/research-mcp/oauth/authorization-server-metadata")
async def research_authorization_server_metadata():
    _require_enabled()
    return authorization_server_metadata_payload()


@router.get("/api/research-mcp/oauth/jwks")
async def research_jwks_endpoint():
    _require_enabled()
    return workspace_jwks()


@router.post("/api/research-mcp/oauth/register", status_code=201)
async def register_research_client(
    request: Request, db: AsyncSession = Depends(get_db)
):
    _require_enabled()
    if not settings.RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED:
        return _oauth_error(
            WorkspaceOAuthError(
                "invalid_client_metadata",
                "Dynamic registration is unavailable",
                status_code=403,
            )
        )
    try:
        payload = await parse_dynamic_client_registration_payload(request)
        client_name_value = payload.get("client_name")
        client_name = (
            client_name_value.strip() if isinstance(client_name_value, str) else ""
        )
        redirect_values = payload.get("redirect_uris")
        if not 1 <= len(client_name) <= 200:
            raise WorkspaceOAuthError(
                "invalid_client_metadata", "client_name is required"
            )
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
            or not all(isinstance(value, str) for value in grant_types)
            or "authorization_code" not in grant_types
            or set(grant_types) - {"authorization_code", "refresh_token"}
            or not isinstance(response_types, list)
            or not all(isinstance(value, str) for value in response_types)
            or response_types != ["code"]
            or payload.get("token_endpoint_auth_method", "none") != "none"
        ):
            raise WorkspaceOAuthError(
                "invalid_client_metadata",
                "Only public authorization-code clients with S256 PKCE are supported",
            )
        now = datetime.now(timezone.utc)
        client = WorkspaceMCPClient(
            client_id="research." + secrets.token_urlsafe(32),
            client_name=client_name,
            redirect_uris=redirect_uris,
            grant_types=grant_types,
            response_types=["code"],
            token_endpoint_auth_method="none",
            software_id=str(payload.get("software_id") or "")[:200] or None,
            software_version=str(payload.get("software_version") or "")[:100] or None,
            expires_at=now
            + timedelta(days=settings.RESEARCH_MCP_CLIENT_REGISTRATION_DAYS),
        )
        db.add(client)
        await db.commit()
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


@router.get("/api/research-mcp/oauth/authorize")
async def begin_research_authorization(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(..., min_length=1, max_length=200),
    redirect_uri: str = Query(..., min_length=1, max_length=1000),
    scope: str = Query(..., min_length=1, max_length=200),
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
        if resource != research_resource_uri():
            raise WorkspaceOAuthError("invalid_target", "OAuth resource is invalid")
        scopes = normalized_research_scopes(scope)
        validate_pkce_challenge(code_challenge, code_challenge_method)
        request_id = await save_research_authorization_request(
            request,
            {
                "client_id": client.client_id,
                "redirect_uri": redirect_uri,
                "scopes": sorted(scopes),
                "state": state,
                "code_challenge": code_challenge,
                "resource": research_resource_uri(),
                "created_at": int(time.time()),
            },
        )
        return RedirectResponse(
            _redirect(
                f"{settings.FRONTEND_URL.rstrip('/')}/research-mcp/authorize",
                request_id=request_id,
            ),
            status_code=302,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except WorkspaceOAuthError as exc:
        return _oauth_error(exc)


@router.get("/api/research-mcp/oauth/requests/{request_id}")
async def get_research_authorization_request(
    request_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    ensure_mcp_product_access(user.tenant)
    pending = await load_research_authorization_request(request, request_id)
    if pending is None:
        raise HTTPException(status_code=410, detail="Authorization request expired")
    client = await _active_client(db, str(pending.get("client_id") or ""))
    scopes = frozenset(str(value) for value in pending.get("scopes", []))
    if RESEARCH_SCOPE not in scopes or scopes - RESEARCH_OAUTH_SCOPE_LABELS.keys():
        raise HTTPException(status_code=403, detail="Requested access is unavailable")
    return {
        "request_id": request_id,
        "client": {"id": client.client_id, "name": client.client_name},
        "organization": {"id": str(user.tenant_id), "name": user.tenant.name},
        "user": {"id": str(user.id), "email": user.email, "name": user.full_name},
        "scopes": [
            {"name": scope, "label": RESEARCH_OAUTH_SCOPE_LABELS[scope]}
            for scope in sorted(scopes)
        ],
        "notice": CONSENT_NOTICE,
        "expires_in": settings.RESEARCH_MCP_AUTH_CODE_TTL_SECONDS,
    }


@router.post("/api/research-mcp/oauth/requests/{request_id}/decision")
async def decide_research_authorization(
    request_id: str,
    decision: ConsentDecision,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    ensure_mcp_product_access(user.tenant)
    pending = await claim_research_authorization_request(request, request_id)
    if pending is None:
        raise HTTPException(status_code=410, detail="Authorization request expired")
    code = ""
    committed = False
    try:
        client = await _active_client(db, str(pending.get("client_id") or ""))
        redirect_uri = str(pending.get("redirect_uri") or "")
        if redirect_uri not in client.redirect_uri_set:
            raise HTTPException(
                status_code=400, detail="OAuth redirect no longer matches"
            )
        if not decision.approved:
            await finalize_research_authorization_request(request, request_id)
            return {
                "redirect_to": _redirect(
                    redirect_uri,
                    error="access_denied",
                    state=str(pending.get("state") or "") or None,
                )
            }
        scopes = frozenset(str(value) for value in pending.get("scopes", []))
        if RESEARCH_SCOPE not in scopes or scopes - RESEARCH_OAUTH_SCOPE_LABELS.keys():
            raise HTTPException(
                status_code=403, detail="Requested access is unavailable"
            )
        previous = await db.scalar(
            select(WorkspaceMCPGrant).where(
                WorkspaceMCPGrant.tenant_id == user.tenant_id,
                WorkspaceMCPGrant.user_id == user.id,
                WorkspaceMCPGrant.client_id == client.client_id,
                WorkspaceMCPGrant.status == "active",
                WorkspaceMCPGrant.revoked_at.is_(None),
            )
        )
        grant = await replace_active_research_grant(
            db, tenant_id=user.tenant_id, user_id=user.id, client=client
        )
        code = await save_research_authorization_code(
            request,
            {
                "tenant_id": str(user.tenant_id),
                "user_id": str(user.id),
                "grant_id": str(grant.id),
                "client_id": client.client_id,
                "redirect_uri": redirect_uri,
                "scopes": [RESEARCH_SCOPE],
                "code_challenge": str(pending.get("code_challenge") or ""),
                "resource": research_resource_uri(),
            },
        )
        await db.commit()
        committed = True
        try:
            await finalize_research_authorization_request(request, request_id)
            if previous is not None:
                await revoke_research_grant_refresh_tokens(request, previous.id)
        except Exception:
            # The database grant is authoritative; delayed Redis cleanup cannot
            # make the newly issued authorization code invalid.
            logger.exception(
                "Research MCP post-consent cleanup failed request_id=%s", request_id
            )
        return {
            "redirect_to": _redirect(
                redirect_uri,
                code=code,
                state=str(pending.get("state") or "") or None,
            )
        }
    except Exception:
        if not committed:
            await db.rollback()
            if code:
                try:
                    await delete_research_authorization_code(request, code)
                except Exception:
                    logger.exception(
                        "Research MCP failed to remove an uncommitted code"
                    )
            try:
                await restore_research_authorization_request(request, request_id)
            except Exception:
                logger.exception(
                    "Research MCP failed to restore consent request request_id=%s",
                    request_id,
                )
        raise


@router.post("/api/research-mcp/oauth/token")
async def research_token(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    cleanup_family: str | None = None
    try:
        content_type = (
            request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        )
        if content_type != "application/x-www-form-urlencoded":
            raise WorkspaceOAuthError(
                "invalid_request",
                "OAuth token requests must use application/x-www-form-urlencoded",
            )
        form = await request.form()
        grant_type = str(form.get("grant_type") or "")
        client = await _active_client(db, str(form.get("client_id") or ""))
        if str(form.get("resource") or "") != research_resource_uri():
            raise WorkspaceOAuthError("invalid_target", "OAuth resource is invalid")
        if grant_type == "authorization_code":
            code = str(form.get("code") or "")
            payload = await load_research_authorization_code(request, code)
            if payload is None:
                raise WorkspaceOAuthError(
                    "invalid_grant", "Authorization code is invalid"
                )
            redirect_uri = str(form.get("redirect_uri") or "")
            if (
                payload.get("client_id") != client.client_id
                or payload.get("redirect_uri") != redirect_uri
                or payload.get("resource") != research_resource_uri()
                or not verify_pkce(
                    str(form.get("code_verifier") or ""),
                    str(payload.get("code_challenge") or ""),
                )
            ):
                raise WorkspaceOAuthError(
                    "invalid_grant", "Authorization code binding is invalid"
                )
            tenant_id = uuid.UUID(str(payload["tenant_id"]))
            user_id = uuid.UUID(str(payload["user_id"]))
            scopes = frozenset(str(value) for value in payload["scopes"])
            grant, user = await _load_actor(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                grant_id=str(payload["grant_id"]),
                client_id=client.client_id,
                scopes=scopes,
            )
            if not await consume_research_authorization_code(request, code, payload):
                raise WorkspaceOAuthError(
                    "invalid_grant", "Authorization code was already used"
                )
            family_id = str(uuid.uuid4())
        elif grant_type == "refresh_token":
            status, payload = await consume_research_refresh_token(
                request,
                str(form.get("refresh_token") or ""),
                expected_client_id=client.client_id,
                expected_resource=research_resource_uri(),
            )
            if status == "replay" and isinstance(payload, str):
                await revoke_research_refresh_family(request, payload)
                raise WorkspaceOAuthError(
                    "invalid_grant", "Refresh token reuse was detected"
                )
            if status != "consumed" or not isinstance(payload, dict):
                raise WorkspaceOAuthError("invalid_grant", "Refresh token is invalid")
            family_id = str(payload.get("family_id") or "")
            cleanup_family = family_id
            tenant_id = uuid.UUID(str(payload["tenant_id"]))
            user_id = uuid.UUID(str(payload["user_id"]))
            scopes = frozenset(str(value) for value in payload["scopes"])
            grant, user = await _load_actor(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                grant_id=str(payload["grant_id"]),
                client_id=client.client_id,
                scopes=scopes,
            )
        else:
            raise WorkspaceOAuthError(
                "unsupported_grant_type", "Unsupported OAuth grant type"
            )
        access_token, _jti, expires_in = mint_research_access_token(
            user_id=user.id,
            tenant_id=tenant_id,
            client_id=client.client_id,
            grant_id=grant.id,
            scopes=scopes,
        )
        refresh_token = await issue_research_refresh_token(
            request,
            user_id=user.id,
            tenant_id=tenant_id,
            client_id=client.client_id,
            grant_id=grant.id,
            scopes=scopes,
            family_id=family_id,
        )
        cleanup_family = family_id
        client.last_used_at = datetime.now(timezone.utc)
        await db.commit()
        cleanup_family = None
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": expires_in,
                "refresh_token": refresh_token,
                "scope": RESEARCH_SCOPE,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except WorkspaceOAuthError as exc:
        await db.rollback()
        if cleanup_family:
            await revoke_research_refresh_family(request, cleanup_family)
        return _oauth_error(exc)
    except (KeyError, TypeError, ValueError):
        await db.rollback()
        if cleanup_family:
            await revoke_research_refresh_family(request, cleanup_family)
        return _oauth_error(
            WorkspaceOAuthError("invalid_grant", "OAuth token request is invalid")
        )
    except Exception:
        await db.rollback()
        if cleanup_family:
            try:
                await revoke_research_refresh_family(request, cleanup_family)
            except Exception:
                logger.exception(
                    "Research MCP failed-exchange refresh cleanup was incomplete"
                )
        raise


@router.post("/api/research-mcp/oauth/revoke")
async def revoke_research_token(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    form = await request.form()
    token = str(form.get("token") or "")
    client_id = str(form.get("client_id") or "")
    try:
        await _active_client(db, client_id)
        if token.startswith("rmr_"):
            status, payload = await consume_research_refresh_token(
                request,
                token,
                expected_client_id=client_id,
                expected_resource=research_resource_uri(),
            )
            if status == "consumed" and isinstance(payload, dict):
                if payload.get("client_id") == client_id and payload.get("family_id"):
                    await revoke_research_refresh_family(
                        request, str(payload["family_id"])
                    )
            elif status == "replay" and isinstance(payload, str):
                await revoke_research_refresh_family(request, payload)
        else:
            claims = decode_research_access_token(token)
            if claims.get("client_id") == client_id:
                redis = getattr(request.app.state, "redis", None)
                if redis is None:
                    raise RedisError("revocation store unavailable")
                await redis.setex(
                    f"research_mcp:jti:{claims['jti']}",
                    settings.RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES * 60,
                    b"1",
                )
    except RedisError:
        return JSONResponse(
            status_code=503, content={"error": "temporarily_unavailable"}
        )
    except Exception:
        pass
    return JSONResponse({}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/api/research-mcp/grants")
async def list_research_grants(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    _require_enabled()
    ensure_mcp_product_access(user.tenant)
    grants = list(
        (
            await db.scalars(
                select(WorkspaceMCPGrant)
                .where(
                    WorkspaceMCPGrant.tenant_id == user.tenant_id,
                    WorkspaceMCPGrant.user_id == user.id,
                    WorkspaceMCPGrant.client_id.like("research.%"),
                    WorkspaceMCPGrant.consent_version == "research-mcp-v1",
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
                "scopes": [
                    {
                        "name": RESEARCH_SCOPE,
                        "label": RESEARCH_SCOPE_LABELS[RESEARCH_SCOPE],
                    }
                ],
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


@router.post("/api/research-mcp/grants/{grant_id}/revoke")
async def revoke_research_grant(
    grant_id: uuid.UUID,
    payload: RevokeGrantRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    grant = await db.scalar(
        select(WorkspaceMCPGrant).where(
            WorkspaceMCPGrant.id == grant_id,
            WorkspaceMCPGrant.tenant_id == user.tenant_id,
            WorkspaceMCPGrant.user_id == user.id,
            WorkspaceMCPGrant.client_id.like("research.%"),
            WorkspaceMCPGrant.consent_version == "research-mcp-v1",
        )
    )
    if grant is None:
        raise HTTPException(status_code=404, detail="Research connection not found")
    if grant.status == "active":
        grant.status = "revoked"
        grant.revoked_at = datetime.now(timezone.utc)
        grant.revoked_by_user_id = user.id
        grant.revocation_reason = payload.reason.strip()[:500] or "Disconnected by user"
        await db.commit()
        try:
            await revoke_research_grant_refresh_tokens(request, grant.id)
        except Exception:
            # The persisted grant is checked on every token/tool request.
            logger.exception("Research MCP post-revocation cleanup failed")
    return {"id": str(grant.id), "status": grant.status}
