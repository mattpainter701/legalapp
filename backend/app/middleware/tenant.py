import time as _time
import uuid
from datetime import datetime, timezone

from fastapi import Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError, jwt

from app.database import get_db
from app.config import get_settings
from app.services.tenant_state import require_active_tenant

settings = get_settings()

SKIP_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/billing/webhook",
    "/api/matters/esign/webhooks",
}

SKIP_PREFIXES = (
    "/auth/",
    "/api/auth/",
    "/api/integrations/zoom-phone/webhook",
    "/api/integrations/teams/voice/webhook",
)

LICENSE_EXEMPT_PREFIXES = (
    "/auth/",
    "/api/auth/",
    "/portal/",
    "/api/portal/",
    # Research is a separately billed public-authority product. A user may
    # connect/revoke it without holding a Workspace seat; each handler still
    # enforces the Research entitlement and active billing state.
    "/api/research-mcp/",
    # Research-only tenant administrators must also be able to issue/revoke
    # header credentials and inspect their metered usage. Tool execution paths
    # are deliberately not exempted here.
    "/api/mcp/product-keys/",
)

LICENSE_EXEMPT_PATHS = {
    "/api/mcp/product-keys",
    "/api/mcp/usage",
}


def _is_license_exempt(request: Request) -> bool:
    path = request.url.path
    if path in LICENSE_EXEMPT_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in LICENSE_EXEMPT_PREFIXES):
        return True
    if request.method == "GET" and path.rstrip("/") == "/api/plugins":
        return True
    return False


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth and public paths
        if path in SKIP_PATHS or any(path.startswith(p) for p in SKIP_PREFIXES):
            return await call_next(request)

        # Try to get token from cookie first, then fall back to Authorization header
        token = request.cookies.get("access_token")
        if not token:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return await call_next(request)
            token = auth_header.split(" ", 1)[1]
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
            )
            jti: str | None = payload.get("jti")
            if jti:
                redis = getattr(request.app.state, "redis", None)
                blacklisted = False
                if redis:
                    # Redis is the authoritative, cross-worker revocation store.
                    blacklisted = await redis.exists(f"jti:{jti}")
                else:
                    # Per-worker in-memory fallback: dev-only, NOT a reliable
                    # revocation guarantee in a multi-worker deployment.
                    blacklist = getattr(request.app.state, "jti_blacklist", {})
                    ts = blacklist.get(jti)
                    if ts and _time.time() < ts:
                        blacklisted = True
                if blacklisted:
                    # Reject revoked tokens outright instead of passing the
                    # request through unauthenticated.
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Token has been revoked"},
                    )

            tenant_id: str = payload.get("tenant_id")
            user_id: str = payload.get("sub")

            # Keep signed entitlement claims available to handlers making
            # provider-side safety decisions. They must still be compared
            # with authoritative database state before any side effect.
            request.state.signed_plan = payload.get("plan")
            request.state.signed_billing_tier = payload.get("billing_tier")

            if tenant_id:
                request.state.tenant_id = tenant_id
            if user_id:
                request.state.user_id = user_id

        except JWTError:
            pass

        return await call_next(request)


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    """Dependency that reads request.state and queries user from DB."""
    from app.database import set_tenant_context
    from app.models.user import User

    user_id = getattr(request.state, "user_id", None)
    # TenantMiddleware sets tenant_id for non-auth routes; auth routes get it from JWT below.
    tenant_id = getattr(request.state, "tenant_id", None)

    if not user_id:
        # /api/auth/* routes bypass TenantMiddleware — parse the token directly.
        token = request.cookies.get("access_token")
        if not token:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Not authenticated")
            token = auth_header.split(" ", 1)[1]
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
            )
            jti: str | None = payload.get("jti")
            if jti:
                redis = getattr(request.app.state, "redis", None)
                if redis:
                    blacklisted = await redis.exists(f"jti:{jti}")
                else:
                    blacklist = getattr(request.app.state, "jti_blacklist", {})
                    ts = blacklist.get(jti)
                    blacklisted = ts and _time.time() < ts
                if blacklisted:
                    raise HTTPException(
                        status_code=401, detail="Token has been revoked"
                    )
            user_id = payload.get("sub")
            tenant_id = payload.get("tenant_id")
            request.state.signed_plan = payload.get("plan")
            request.state.signed_billing_tier = payload.get("billing_tier")
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid token")

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Set the tenant RLS context before querying the users table.
    # clarity_app role has no BYPASSRLS, so without this the policy filters
    # tenant_id against NULL and returns no rows → "User not found".
    if tenant_id:
        await set_tenant_context(db, str(tenant_id))

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")
    require_active_tenant(user.tenant)
    if not user.license_active and not _is_license_exempt(request):
        raise HTTPException(status_code=403, detail="Standard license required")

    return user


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)):
    """FastAPI dependency that enforces admin access (admin_settings capability)."""
    from app.services.rbac_service import get_user_capabilities

    user = await get_current_user(request, db)
    caps = await get_user_capabilities(db, user.id)
    if "admin_settings" in caps or user.role == "admin":  # legacy fallback
        return user
    raise HTTPException(status_code=403, detail="Admin access required")


class PortalContext:
    """Resolved identity for a mediation-portal request.

    Either a magic-link party (``user`` is None, scoped by the token's claims)
    or a firm client (role="client") whose ``user`` is linked to a
    ``MediationParty`` on the requested case.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        party_id: str,
        party_role: str,
        case_id: str | None = None,
        invite_id: str | None = None,
        user=None,
    ):
        self.tenant_id = tenant_id
        self.party_id = party_id
        self.party_role = party_role
        self.case_id = case_id
        self.invite_id = invite_id
        self.user = user

    @property
    def is_magic(self) -> bool:
        return self.user is None


def _read_token(request: Request) -> str | None:
    token = request.cookies.get("mediation_portal_token")
    if token:
        return token
    token = request.cookies.get("access_token")
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1]
    return None


async def get_portal_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
    case_id: str | None = None,
) -> "PortalContext":
    """Authenticate a mediation-portal request.

    Accepts a portal-scoped JWT (``portal: true``) OR a firm client login
    (``role == "client"``). For client logins, resolves the MediationParty
    linking the user to ``case_id`` (required for client access). Always sets
    the RLS tenant context before returning.
    """
    from app.database import set_tenant_context
    from app.models.mediation import MediationInvite, MediationParty
    from app.models.tenant import Tenant
    from app.models.user import User

    token = _read_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # 1. Magic-link party token.
    if payload.get("portal") is True:
        # Revocation check — allows revoking a mediation invite mid-session.
        jti: str | None = payload.get("jti")
        if jti:
            redis = getattr(request.app.state, "redis", None)
            if redis:
                if await redis.exists(f"jti:{jti}"):
                    raise HTTPException(
                        status_code=401, detail="Portal session has been revoked"
                    )
            else:
                import time as _time

                blacklist = getattr(request.app.state, "jti_blacklist", {})
                ts = blacklist.get(jti)
                if ts and _time.time() < ts:
                    raise HTTPException(
                        status_code=401, detail="Portal session has been revoked"
                    )

        tenant_claim = payload.get("tenant_id")
        invite_id = payload.get("invite_id")
        case_claim = payload.get("case_id")
        party_claim = payload.get("party_id")
        if not all((tenant_claim, invite_id, case_claim, party_claim)):
            raise HTTPException(status_code=401, detail="Invalid portal session")
        try:
            tenant_id = uuid.UUID(str(tenant_claim))
        except (TypeError, ValueError):
            raise HTTPException(status_code=401, detail="Invalid portal session")

        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if tenant is None:
            raise HTTPException(status_code=401, detail="Invalid portal session")
        require_active_tenant(tenant)

        await set_tenant_context(db, str(tenant_id))
        invite_result = await db.execute(
            select(MediationInvite).where(
                MediationInvite.id == invite_id,
                MediationInvite.tenant_id == tenant_id,
                MediationInvite.case_id == case_claim,
                MediationInvite.party_id == party_claim,
            )
        )
        invite = invite_result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if invite is None or invite.revoked or invite.expires_at < now:
            raise HTTPException(
                status_code=401, detail="Portal session has been revoked"
            )
        ctx = PortalContext(
            tenant_id=str(tenant_id),
            party_id=party_claim,
            party_role=payload.get("party_role"),
            case_id=case_claim,
            invite_id=invite_id,
            user=None,
        )
        return ctx

    # 2. Firm client login.
    user_claim = payload.get("sub")
    tenant_claim = payload.get("tenant_id")
    if not user_claim or not tenant_claim:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        user_id = uuid.UUID(str(user_claim))
        tenant_id = uuid.UUID(str(tenant_claim))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Not authenticated")

    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    require_active_tenant(tenant)

    # Users are protected by FORCE RLS in production. Establish the signed
    # tenant boundary before lookup and constrain both identity claims so a
    # valid user id cannot be paired with another tenant id.
    await set_tenant_context(db, str(tenant_id))
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.role != "client":
        raise HTTPException(status_code=403, detail="Portal access only")
    if not case_id:
        raise HTTPException(status_code=400, detail="case_id required")

    party_result = await db.execute(
        select(MediationParty).where(
            MediationParty.case_id == case_id,
            MediationParty.user_id == user.id,
            MediationParty.tenant_id == user.tenant_id,
        )
    )
    party = party_result.scalar_one_or_none()
    if party is None:
        raise HTTPException(status_code=403, detail="Not a party to this case")
    return PortalContext(
        tenant_id=str(user.tenant_id),
        party_id=str(party.id),
        party_role=party.role,
        case_id=str(case_id),
        user=user,
    )
