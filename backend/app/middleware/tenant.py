import time as _time
from fastapi import Depends, Request, HTTPException
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
                    blacklisted = await redis.exists(f"jti:{jti}")
                else:
                    blacklist = getattr(request.app.state, "jti_blacklist", {})
                    ts = blacklist.get(jti)
                    if ts and _time.time() < ts:
                        blacklisted = True
                if blacklisted:
                    return await call_next(request)

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
                    raise HTTPException(status_code=401, detail="Token has been revoked")
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
