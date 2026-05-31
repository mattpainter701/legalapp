import base64
import json as _json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.tenant import get_current_user
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import TokenResponse, UserInfo

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])

# OAuth state TTL in seconds
_STATE_TTL = 600


def _state_key(state: str) -> str:
    return f"oauth:state:{state}"


async def _save_state(request: Request, state: str) -> None:
    """Persist OAuth state token in Redis (falls back to in-process dict if Redis is absent)."""
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.setex(_state_key(state), _STATE_TTL, "1")
    else:
        _fallback_states[state] = True


async def _consume_state(request: Request, state: str) -> bool:
    """Return True and delete if state exists; False otherwise."""
    redis = getattr(request.app.state, "redis", None)
    if redis:
        deleted = await redis.delete(_state_key(state))
        return bool(deleted)
    return _fallback_states.pop(state, None) is not None


# In-process fallback — fine for single-worker dev, not for prod multi-worker
_fallback_states: dict[str, bool] = {}


# ── Token helpers ──────────────────────────────────────────────────────────────

def _create_access_token(user: User, tenant: Tenant) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": user.email,
        "billing_tier": tenant.billing_tier,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ── Tenant / user upsert helpers ───────────────────────────────────────────────

async def _get_or_create_tenant(
    db: AsyncSession, domain: str, tenant_name: str
) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.domain == domain))
    tenant = result.scalar_one_or_none()

    if tenant is None:
        tenant = Tenant(
            id=uuid.uuid4(),
            name=tenant_name,
            domain=domain,
            billing_tier="payg",
            is_active=True,
        )
        db.add(tenant)
        await db.flush()

    return tenant


async def _get_or_create_user(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    email: str,
    full_name: Optional[str],
    provider: str,
    sub: str,
) -> User:
    # Try by oauth subject first (most specific)
    result = await db.execute(
        select(User).where(
            User.tenant_id == tenant_id,
            User.oauth_provider == provider,
            User.oauth_subject == sub,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Fall back to email match within tenant
        result = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email,
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            count_result = await db.execute(
                select(User).where(User.tenant_id == tenant_id)
            )
            is_first = len(count_result.scalars().all()) == 0

            user = User(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                email=email,
                full_name=full_name,
                role="admin" if is_first else "user",
                oauth_provider=provider,
                oauth_subject=sub,
                is_active=True,
            )
            db.add(user)
            await db.flush()
        else:
            user.oauth_provider = provider
            user.oauth_subject = sub
            if full_name and not user.full_name:
                user.full_name = full_name

    return user


# ── Microsoft OAuth ────────────────────────────────────────────────────────────

@router.get("/microsoft/login")
async def microsoft_login(request: Request):
    if not settings.MICROSOFT_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Microsoft OAuth not configured")

    state = secrets.token_urlsafe(32)
    await _save_state(request, state)

    ms_tenant = settings.MICROSOFT_TENANT_ID
    redirect_uri = f"{settings.BACKEND_URL}/api/auth/microsoft/callback"

    authorize_url = (
        f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/authorize"
        f"?client_id={settings.MICROSOFT_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope=openid+email+profile+User.Read"
        f"&state={state}"
        f"&response_mode=query"
    )
    return RedirectResponse(url=authorize_url)


@router.get("/microsoft/callback")
async def microsoft_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not await _consume_state(request, state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    redirect_uri = f"{settings.BACKEND_URL}/api/auth/microsoft/callback"
    ms_tenant = settings.MICROSOFT_TENANT_ID
    token_url = f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/token"

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            token_url,
            data={
                "client_id": settings.MICROSOFT_CLIENT_ID,
                "client_secret": settings.MICROSOFT_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": "openid email profile User.Read",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to exchange Microsoft authorization code")

        token_data = token_response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="No access token from Microsoft")

        graph_response = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if graph_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch Microsoft profile")

        profile = graph_response.json()

    email = (profile.get("mail") or profile.get("userPrincipalName") or "").lower().strip()
    full_name = profile.get("displayName")
    ms_sub = profile.get("id", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in Microsoft profile")

    domain = email.split("@")[-1]
    tenant_name = domain.split(".")[0].capitalize()

    async with db.begin():
        tenant = await _get_or_create_tenant(db, domain, tenant_name)
        user = await _get_or_create_user(db, tenant.id, email, full_name, "microsoft", ms_sub)

    jwt_token = _create_access_token(user, tenant)
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback?token={jwt_token}&tenant_id={tenant.id}"
    )


# ── Google OAuth ───────────────────────────────────────────────────────────────

# Google's JWKS endpoint — used to verify id_token signatures
_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


async def _verify_google_id_token(id_token: str) -> dict:
    """
    Verify a Google id_token against Google's public JWKS and return claims.
    Falls back to unverified decode in DEV_MODE only.
    """
    if settings.DEV_MODE:
        # Dev shortcut — skip signature verification
        parts = id_token.split(".")
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        return _json.loads(base64.urlsafe_b64decode(padded))

    async with httpx.AsyncClient() as client:
        jwks_response = await client.get(_GOOGLE_JWKS_URL)
        if jwks_response.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch Google JWKS")
        jwks = jwks_response.json()

    from jose import jwk, jws
    from jose.utils import base64url_decode

    # Parse the JWT header to find the key ID
    header_segment = id_token.split(".")[0]
    padded = header_segment + "=" * (4 - len(header_segment) % 4)
    header = _json.loads(base64.urlsafe_b64decode(padded))
    kid = header.get("kid")

    # Find the matching public key
    matching_key = None
    for key_data in jwks.get("keys", []):
        if key_data.get("kid") == kid:
            matching_key = key_data
            break

    if matching_key is None:
        raise HTTPException(status_code=400, detail="No matching Google public key for token kid")

    try:
        public_key = jwk.construct(matching_key)
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_CLIENT_ID,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Google id_token verification failed: {exc}")

    return claims


@router.get("/google/login")
async def google_login(request: Request):
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    state = secrets.token_urlsafe(32)
    await _save_state(request, state)

    redirect_uri = f"{settings.BACKEND_URL}/api/auth/google/callback"

    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope=openid+email+profile"
        f"&state={state}"
        f"&access_type=offline"
    )
    return RedirectResponse(url=authorize_url)


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not await _consume_state(request, state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    redirect_uri = f"{settings.BACKEND_URL}/api/auth/google/callback"

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to exchange Google authorization code")

        token_data = token_response.json()

    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="No id_token from Google")

    claims = await _verify_google_id_token(id_token)

    email = claims.get("email", "").lower().strip()
    full_name = claims.get("name")
    google_sub = claims.get("sub", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in Google profile")

    domain = email.split("@")[-1]
    tenant_name = domain.split(".")[0].capitalize()

    async with db.begin():
        tenant = await _get_or_create_tenant(db, domain, tenant_name)
        user = await _get_or_create_user(db, tenant.id, email, full_name, "google", google_sub)

    jwt_token = _create_access_token(user, tenant)
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback?token={jwt_token}&tenant_id={tenant.id}"
    )


# ── Logout / Me ────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout():
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserInfo)
async def get_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    return UserInfo(
        id=str(user.id),
        tenant_id=str(user.tenant_id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        billing_tier=user.tenant.billing_tier if user.tenant else "payg",
    )
