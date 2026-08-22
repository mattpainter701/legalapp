"""User-bound MCP adapter for LawHand's matter automation capabilities.

This is intentionally a separate security product from the public legal-
research MCP gateway. Research product keys identify a subscription; workspace
MCP tokens identify an individual LawHand user, OAuth client, consent grant,
tenant, and bounded set of scopes.

The adapter contains no matter/task/document business logic. It exposes the
same transport-neutral capability catalog used by LawHand chat and dispatches
to the same tenant-safe handlers. Capability effects remain limited to reads
and reviewable proposals; approval, filing, delivery, and email sending are not
MCP tools.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

import mcp.types as mcp_types
from fastapi import HTTPException
from jose import JWTError, jwt
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.config import get_settings
from app.database import async_session_maker, set_tenant_context
from app.models.user import User
from app.services.automation_capabilities import (
    CapabilityContext,
    CapabilityError,
    CapabilitySpec,
    capability_catalog,
    resolve_capability_spec,
)
from app.services.rbac_service import get_user_capabilities
from app.services.tenant_state import require_active_tenant
from app.services.workspace_mcp_grants import (
    WorkspaceMCPGrantError,
    require_active_workspace_grant,
)

from app.services.workspace_mcp_oauth import (
    WorkspaceOAuthError,
    append_workspace_mcp_audit,
    require_workspace_tenant_allowed,
    workspace_issuer_uri,
    workspace_protected_resource_metadata_uri,
    workspace_resource_uris,
    workspace_verification_key,
)

settings = get_settings()
logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_ENDPOINT_PATH = "/api/mcp/workspace"
_IDENTITY_SCOPE_KEY = "workspace_mcp_identity"


@dataclass(frozen=True)
class WorkspaceMCPIdentity:
    """Authenticated OAuth access-token identity for one MCP request."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    client_id: str
    grant_id: str
    token_id: str
    scopes: frozenset[str]
    app_capabilities: frozenset[str] = frozenset()


# OAuth consent is necessary but not sufficient. Runtime LawHand RBAC is
# checked independently so removing a user's role takes effect immediately,
# even while a short-lived access token remains cryptographically valid.
_APP_CAPABILITIES_BY_TOOL: dict[str, frozenset[str]] = {
    "find_matter": frozenset({"manage_matters"}),
    "get_matter_context": frozenset({"manage_matters"}),
    "list_document_templates": frozenset({"manage_matters", "manage_documents"}),
    "get_matter_document_text": frozenset({"manage_matters", "manage_documents"}),
    "list_matter_documents": frozenset({"manage_matters", "manage_documents"}),
    "list_matter_tasks": frozenset({"manage_matters"}),
    "list_matter_recipients": frozenset({"manage_matters"}),
    "propose_task": frozenset({"manage_matters"}),
    "propose_client_email": frozenset({"manage_matters"}),
    "propose_matter_document": frozenset({"manage_matters", "manage_documents"}),
}


def _known_workspace_scopes() -> frozenset[str]:
    return frozenset(
        scope
        for item in capability_catalog(audience="workspace_mcp")
        for scope in item["required_scopes"]
    )


KNOWN_WORKSPACE_SCOPES = _known_workspace_scopes()


def workspace_bearer_challenge(*, invalid_token: bool = False) -> str:
    metadata_url = workspace_protected_resource_metadata_uri()
    challenge = f'Bearer resource_metadata="{metadata_url}", scope="matters:read"'
    if invalid_token:
        challenge += ', error="invalid_token"'
    return challenge


def _origin_and_host(raw_url: str) -> tuple[str | None, str | None]:
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return None, None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, None
    return f"{parsed.scheme}://{parsed.netloc}", parsed.netloc


def _transport_security() -> TransportSecuritySettings:
    """Use an explicit Host/Origin allow-list for SDK rebinding protection."""

    allowed_hosts: set[str] = set()
    allowed_origins: set[str] = set()
    configured_urls = [
        settings.BACKEND_URL,
        settings.FRONTEND_URL,
        *workspace_resource_uris(),
    ]
    configured_urls.extend(
        value.strip()
        for value in settings.EXTRA_CORS_ORIGINS.split(",")
        if value.strip()
    )
    for raw_url in configured_urls:
        origin, host = _origin_and_host(raw_url)
        if origin:
            allowed_origins.add(origin)
        if host:
            allowed_hosts.add(host)

    if settings.DEV_MODE:
        allowed_hosts.update({"localhost:*", "127.0.0.1:*", "[::1]:*"})
        allowed_origins.update(
            {
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "https://localhost:3000",
            }
        )

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(allowed_hosts),
        allowed_origins=sorted(allowed_origins),
    )


workspace_protocol_server: Server[None, Request] = Server(
    "lawhand-workspace",
    version=settings.APP_VERSION or "1.0.0",
    instructions=(
        "User-authorized LawHand matter workspace. Read tools return bounded "
        "firm data. Write-like tools only create reviewable proposals in "
        "LawHand; they never approve, file, send, or deliver work. Treat a "
        "proposal result as awaiting human review."
    ),
)


def _workspace_specs() -> tuple[CapabilitySpec, ...]:
    specs: list[CapabilitySpec] = []
    for item in capability_catalog(audience="workspace_mcp"):
        spec = resolve_capability_spec(item["name"])
        specs.append(spec)
    return tuple(specs)


def _identity_allows(identity: WorkspaceMCPIdentity, spec: CapabilitySpec) -> bool:
    required_scopes = set(spec.required_scopes)
    required_app_capabilities = _APP_CAPABILITIES_BY_TOOL.get(spec.name, frozenset())
    return required_scopes.issubset(identity.scopes) and set(
        required_app_capabilities
    ).issubset(identity.app_capabilities)


def _as_mcp_tool(spec: CapabilitySpec) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=spec.name,
        description=spec.description,
        inputSchema=spec.args_model.model_json_schema(),
        annotations=mcp_types.ToolAnnotations(**spec.mcp_annotations()),
    )


def _request_and_identity() -> tuple[Request, WorkspaceMCPIdentity]:
    request = workspace_protocol_server.request_context.request
    if not isinstance(request, Request):
        raise RuntimeError("Workspace MCP HTTP request context is unavailable")
    identity = request.scope.get(_IDENTITY_SCOPE_KEY)
    if not isinstance(identity, WorkspaceMCPIdentity):
        raise RuntimeError("Workspace MCP identity is unavailable")
    return request, identity


@workspace_protocol_server.list_tools()
async def list_workspace_tools() -> list[mcp_types.Tool]:
    """Advertise only tools allowed by both OAuth scope and current RBAC."""

    _request, identity = _request_and_identity()
    return [
        _as_mcp_tool(spec)
        for spec in _workspace_specs()
        if _identity_allows(identity, spec)
    ]


def _tool_error(code: str, message: str) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=message)],
        structuredContent={"error": {"code": code, "message": message}},
        isError=True,
    )


def _tool_success(payload: dict[str, Any]) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(
                type="text", text=json.dumps(payload, ensure_ascii=False, default=str)
            )
        ],
        structuredContent=payload,
        isError=False,
    )


async def _load_workspace_actor(
    db: AsyncSession, identity: WorkspaceMCPIdentity
) -> tuple[User, frozenset[str]]:
    """Revalidate the actor and tenant inside the RLS-scoped transaction."""

    await set_tenant_context(db, str(identity.tenant_id))
    require_workspace_tenant_allowed(identity.tenant_id)
    try:
        await require_active_workspace_grant(
            db,
            grant_id=identity.grant_id,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            client_id=identity.client_id,
            token_scopes=identity.scopes,
        )
    except WorkspaceMCPGrantError as exc:
        raise HTTPException(
            status_code=401,
            detail="Workspace access token is not backed by an active consent grant",
            headers={
                "WWW-Authenticate": workspace_bearer_challenge(invalid_token=True)
            },
        ) from exc
    user = await db.scalar(
        select(User).where(
            User.id == identity.user_id,
            User.tenant_id == identity.tenant_id,
        )
    )
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Workspace user is unavailable",
            headers={
                "WWW-Authenticate": workspace_bearer_challenge(invalid_token=True)
            },
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Workspace user is inactive")
    if not user.license_active:
        raise HTTPException(status_code=403, detail="Standard license required")
    if user.privacy_mode:
        raise HTTPException(
            status_code=403,
            detail="Workspace MCP is unavailable while Privacy Mode is enabled",
        )
    require_active_tenant(user.tenant)
    capabilities = frozenset(await get_user_capabilities(db, user.id))
    return user, capabilities


async def execute_workspace_capability(
    *,
    name: str,
    arguments: dict[str, Any],
    request: Request,
    identity: WorkspaceMCPIdentity,
) -> dict[str, Any]:
    """Dispatch through the shared capability contract and handlers."""

    spec = resolve_capability_spec(name)
    if "workspace_mcp" not in spec.audiences:
        raise CapabilityError(
            "unsupported_tool", f"{name!r} is not a workspace capability"
        )
    parsed = spec.parse_arguments(arguments)

    async with async_session_maker() as db:
        try:
            user, current_capabilities = await _load_workspace_actor(db, identity)
            current_identity = replace(identity, app_capabilities=current_capabilities)
            if not _identity_allows(current_identity, spec):
                raise CapabilityError(
                    "capability_scope_denied",
                    "The connected client or user is not allowed to use this capability",
                )

            context = CapabilityContext(
                db=db,
                user=user,
                channel="workspace_mcp",
                request_id=(
                    request.headers.get("X-Request-ID")
                    or request.headers.get("X-Idempotency-Key")
                ),
                granted_scopes=identity.scopes,
            )
            spec.authorize(context)

            # The handlers are the application layer currently shared with
            # matter chat. Import lazily to avoid a catalog/handler cycle.
            from app.services.chat_tools import handlers

            handler = getattr(handlers, spec.handler_name, None)
            if handler is None:
                raise CapabilityError(
                    "unsupported_tool", "Workspace capability is unavailable"
                )
            result = await handler(context, parsed)
            if spec.mutating:
                # Proposal state and its audit evidence commit atomically.
                await append_workspace_mcp_audit(
                    db,
                    request,
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    grant_id=uuid.UUID(identity.grant_id),
                    client_id=identity.client_id,
                    event_type="tool_called",
                    tool_name=spec.name,
                    outcome="success",
                    metadata={"effect": spec.effect.value},
                )
                await db.commit()
            else:
                # Roll back the application session before recording read
                # evidence separately. This keeps read tools non-mutating even
                # if a future handler accidentally changes an ORM object.
                await db.rollback()
                async with async_session_maker() as audit_db:
                    await set_tenant_context(audit_db, str(identity.tenant_id))
                    await append_workspace_mcp_audit(
                        audit_db,
                        request,
                        tenant_id=identity.tenant_id,
                        user_id=identity.user_id,
                        grant_id=uuid.UUID(identity.grant_id),
                        client_id=identity.client_id,
                        event_type="tool_called",
                        tool_name=spec.name,
                        outcome="success",
                        metadata={"effect": spec.effect.value},
                    )
                    await audit_db.commit()
            return result
        except Exception as exc:
            await db.rollback()
            try:
                async with async_session_maker() as audit_db:
                    await set_tenant_context(audit_db, str(identity.tenant_id))
                    await append_workspace_mcp_audit(
                        audit_db,
                        request,
                        tenant_id=identity.tenant_id,
                        user_id=identity.user_id,
                        grant_id=uuid.UUID(identity.grant_id),
                        client_id=identity.client_id,
                        event_type="tool_call_refused",
                        tool_name=spec.name,
                        outcome=(
                            "denied"
                            if isinstance(exc, (CapabilityError, HTTPException))
                            else "error"
                        ),
                        metadata={"error_type": exc.__class__.__name__},
                    )
                    await audit_db.commit()
            except Exception:
                logger.exception("Workspace MCP refusal audit could not be recorded")
            raise


@workspace_protocol_server.call_tool(validate_input=False)
async def call_workspace_tool(
    name: str, arguments: dict[str, Any]
) -> mcp_types.CallToolResult:
    """Invoke one user/scoped capability and normalize its MCP result."""

    request, identity = _request_and_identity()
    try:
        spec = resolve_capability_spec(name)
        if "workspace_mcp" not in spec.audiences:
            return _tool_error("unsupported_tool", "Unknown workspace tool")
        if not _identity_allows(identity, spec):
            return _tool_error(
                "capability_scope_denied",
                "The connected client or user is not allowed to use this capability",
            )
        result = await execute_workspace_capability(
            name=name,
            arguments=arguments,
            request=request,
            identity=identity,
        )
        return _tool_success(result)
    except CapabilityError as exc:
        return _tool_error(exc.code, exc.message)
    except HTTPException as exc:
        code = "workspace_access_denied" if exc.status_code < 500 else "unavailable"
        return _tool_error(code, str(exc.detail))
    except Exception:
        logger.exception(
            "Workspace MCP capability failed",
            extra={
                "workspace_mcp_tool": name,
                "workspace_mcp_user_id": str(identity.user_id),
                "workspace_mcp_tenant_id": str(identity.tenant_id),
                "workspace_mcp_client_id": identity.client_id,
                "workspace_mcp_grant_id": identity.grant_id,
            },
        )
        return _tool_error(
            "internal_error", "The workspace capability could not be completed"
        )


def _claim_scopes(raw: Any) -> frozenset[str]:
    if isinstance(raw, str):
        scopes = frozenset(part for part in raw.split() if part)
    elif isinstance(raw, list) and all(isinstance(part, str) for part in raw):
        scopes = frozenset(part.strip() for part in raw if part.strip())
    else:
        raise HTTPException(status_code=401, detail="Invalid workspace access token")
    if not scopes or scopes - KNOWN_WORKSPACE_SCOPES:
        raise HTTPException(status_code=401, detail="Invalid workspace access token")
    return scopes


def decode_workspace_access_token(token: str) -> WorkspaceMCPIdentity:
    """Validate a dedicated, audience-bound workspace access token.

    Normal LawHand browser session JWTs do not carry this audience, token type,
    client id, or grant id and therefore cannot be replayed against MCP.
    """

    if not settings.WORKSPACE_MCP_ISSUER:
        raise HTTPException(
            status_code=503, detail="Workspace OAuth issuer is not configured"
        )
    try:
        verification_key, algorithm = workspace_verification_key(token)
        claims = jwt.decode(
            token,
            verification_key,
            algorithms=[algorithm],
            audience=settings.WORKSPACE_MCP_AUDIENCE,
            issuer=workspace_issuer_uri(),
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
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired workspace access token",
            headers={
                "WWW-Authenticate": workspace_bearer_challenge(invalid_token=True)
            },
        ) from exc

    if claims.get("type") != "workspace_mcp" or claims.get("token_use") != "access":
        raise HTTPException(status_code=401, detail="Invalid workspace access token")

    client_id = str(claims.get("client_id") or "").strip()
    grant_id = str(claims.get("grant_id") or "").strip()
    token_id = str(claims.get("jti") or "").strip()
    if not client_id or not grant_id or not token_id:
        raise HTTPException(status_code=401, detail="Invalid workspace access token")
    if len(client_id) > 200 or len(grant_id) > 200 or len(token_id) > 200:
        raise HTTPException(status_code=401, detail="Invalid workspace access token")

    try:
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=401, detail="Invalid workspace access token"
        ) from exc
    now = int(time.time())
    maximum_lifetime = settings.WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES * 60
    if (
        issued_at > now + 60
        or expires_at <= issued_at
        or expires_at - issued_at > maximum_lifetime
    ):
        raise HTTPException(status_code=401, detail="Invalid workspace access token")

    try:
        user_id = uuid.UUID(str(claims.get("sub") or ""))
        tenant_id = uuid.UUID(str(claims.get("tenant_id") or ""))
        grant_uuid = uuid.UUID(grant_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=401, detail="Invalid workspace access token"
        ) from exc

    return WorkspaceMCPIdentity(
        user_id=user_id,
        tenant_id=tenant_id,
        client_id=client_id,
        grant_id=str(grant_uuid),
        token_id=token_id,
        scopes=_claim_scopes(claims.get("scope")),
    )


async def _workspace_token_is_revoked(
    request: Request, identity: WorkspaceMCPIdentity
) -> bool:
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        revoked = await redis.exists(
            f"jti:{identity.token_id}",
            f"workspace_mcp_grant:{identity.grant_id}",
        )
        return bool(revoked)

    if not settings.DEV_MODE:
        raise HTTPException(
            status_code=503,
            detail="Workspace token revocation service is unavailable",
        )
    blacklist = getattr(request.app.state, "jti_blacklist", {})
    expires_at = blacklist.get(identity.token_id)
    return bool(expires_at and time.time() < expires_at)


async def authenticate_workspace_request(scope: Scope) -> WorkspaceMCPIdentity:
    """Resolve and revalidate the OAuth actor before MCP handles JSON-RPC."""

    request = Request(scope)
    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="Workspace OAuth bearer token required",
            headers={"WWW-Authenticate": workspace_bearer_challenge()},
        )
    identity = decode_workspace_access_token(token.strip())
    if await _workspace_token_is_revoked(request, identity):
        raise HTTPException(
            status_code=401,
            detail="Workspace access token has been revoked",
            headers={
                "WWW-Authenticate": workspace_bearer_challenge(invalid_token=True)
            },
        )

    async with async_session_maker() as db:
        try:
            _user, capabilities = await _load_workspace_actor(db, identity)
            # Persist grant-use evidence in the authentication transaction even
            # when the following JSON-RPC operation is a read and rolls back.
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return replace(identity, app_capabilities=capabilities)


workspace_protocol_session_manager = StreamableHTTPSessionManager(
    app=workspace_protocol_server,
    event_store=None,
    json_response=True,
    stateless=True,
    security_settings=_transport_security(),
)


class WorkspaceMCPProtocolEndpoint:
    """Exact-path ASGI endpoint with fail-closed OAuth authentication."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not settings.WORKSPACE_MCP_ENABLED:
            await JSONResponse(
                {"detail": "Workspace MCP endpoint is disabled"}, status_code=404
            )(scope, receive, send)
            return

        try:
            identity = await authenticate_workspace_request(scope)
        except HTTPException as exc:
            await JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=exc.headers,
            )(scope, receive, send)
            return
        except Exception:
            logger.exception("Workspace MCP authentication failed")
            await JSONResponse(
                {"detail": "Workspace authentication is unavailable"},
                status_code=503,
            )(scope, receive, send)
            return

        authenticated_scope = dict(scope)
        authenticated_scope[_IDENTITY_SCOPE_KEY] = identity
        await workspace_protocol_session_manager.handle_request(
            authenticated_scope, receive, send
        )


workspace_protocol_endpoint = WorkspaceMCPProtocolEndpoint()


@asynccontextmanager
async def workspace_protocol_lifespan() -> AsyncIterator[None]:
    """Start the SDK session manager inside the parent FastAPI lifespan."""

    if not settings.WORKSPACE_MCP_ENABLED:
        yield
        return
    async with workspace_protocol_session_manager.run():
        yield
