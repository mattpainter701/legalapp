import time as _time

from fastapi import Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError, jwt

from app.database import get_db
from app.config import get_settings

settings = get_settings()

SKIP_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/api/billing/webhook",
}

SKIP_PREFIXES = (
    "/auth/",
    "/api/auth/",
)


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

            if tenant_id:
                request.state.tenant_id = tenant_id
            if user_id:
                request.state.user_id = user_id

        except JWTError:
            pass

        return await call_next(request)


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    """Dependency that reads request.state and queries user from DB."""
    from app.models.user import User

    user_id = getattr(request.state, "user_id", None)

    if not user_id:
        # Try to parse token directly for routes that bypass middleware
        # Try to get token from cookie first, then fall back to Authorization header
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
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid token")

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")

    return user


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)):
    """
    FastAPI dependency that enforces admin role.
    Usage: admin = await require_admin(request, db)
    Or inject via Depends in routers that need it.
    """
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


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
        user=None,
    ):
        self.tenant_id = tenant_id
        self.party_id = party_id
        self.party_role = party_role
        self.case_id = case_id
        self.user = user

    @property
    def is_magic(self) -> bool:
        return self.user is None


def _read_token(request: Request) -> str | None:
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
    from app.models.mediation import MediationParty
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
        tenant_id = payload.get("tenant_id")
        ctx = PortalContext(
            tenant_id=tenant_id,
            party_id=payload.get("party_id"),
            party_role=payload.get("party_role"),
            case_id=payload.get("case_id"),
            user=None,
        )
        await set_tenant_context(db, str(tenant_id))
        return ctx

    # 2. Firm client login.
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.role != "client":
        raise HTTPException(status_code=403, detail="Portal access only")
    if not case_id:
        raise HTTPException(status_code=400, detail="case_id required")

    await set_tenant_context(db, str(user.tenant_id))
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
