import uuid
import secrets
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

# In-memory state store (use Redis in production)
_oauth_states: dict[str, str] = {}


def _create_access_token(user: User, tenant: Tenant) -> str:
    """Create a JWT access token for the user."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": user.email,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def _get_or_create_tenant(
    db: AsyncSession, domain: str, tenant_name: str
) -> Tenant:
    """Find existing tenant by domain or create a new one."""
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
    """Find existing user or create a new one."""
    # Try to find by oauth_subject and provider first
    result = await db.execute(
        select(User).where(
            User.tenant_id == tenant_id,
            User.oauth_provider == provider,
            User.oauth_subject == sub,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Try by email within tenant
        result = await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == email,
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            # Check if this is the first user in the tenant — make them admin
            count_result = await db.execute(
                select(User).where(User.tenant_id == tenant_id)
            )
            existing_users = count_result.scalars().all()
            role = "admin" if len(existing_users) == 0 else "user"

            user = User(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                email=email,
                full_name=full_name,
                role=role,
                oauth_provider=provider,
                oauth_subject=sub,
                is_active=True,
            )
            db.add(user)
            await db.flush()
        else:
            # Update oauth info if signing in via OAuth for the first time
            user.oauth_provider = provider
            user.oauth_subject = sub
            if full_name and not user.full_name:
                user.full_name = full_name

    return user


# ─────────────────────────────────────────────────────
# Microsoft OAuth
# ─────────────────────────────────────────────────────


@router.get("/microsoft/login")
async def microsoft_login():
    """Redirect user to Microsoft OAuth consent screen."""
    if not settings.MICROSOFT_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Microsoft OAuth not configured")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = "microsoft"

    ms_tenant = settings.MICROSOFT_TENANT_ID
    authorize_url = (
        f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/authorize"
        f"?client_id={settings.MICROSOFT_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={settings.FRONTEND_URL}/api/auth/microsoft/callback"
        f"&scope=openid+email+profile+User.Read"
        f"&state={state}"
        f"&response_mode=query"
    )
    return RedirectResponse(url=authorize_url)


@router.get("/microsoft/callback")
async def microsoft_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Handle Microsoft OAuth callback."""
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    _oauth_states.pop(state, None)

    ms_tenant = settings.MICROSOFT_TENANT_ID
    token_url = f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/token"

    async with httpx.AsyncClient() as client:
        # Exchange authorization code for tokens
        token_response = await client.post(
            token_url,
            data={
                "client_id": settings.MICROSOFT_CLIENT_ID,
                "client_secret": settings.MICROSOFT_CLIENT_SECRET,
                "code": code,
                "redirect_uri": f"{settings.FRONTEND_URL}/api/auth/microsoft/callback",
                "grant_type": "authorization_code",
                "scope": "openid email profile User.Read",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if token_response.status_code != 200:
            raise HTTPException(
                status_code=400, detail="Failed to exchange authorization code"
            )

        token_data = token_response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise HTTPException(status_code=400, detail="No access token received")

        # Get user info from Microsoft Graph
        graph_response = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if graph_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch user profile")

        profile = graph_response.json()

    email = profile.get("mail") or profile.get("userPrincipalName", "")
    full_name = profile.get("displayName")
    ms_sub = profile.get("id", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in Microsoft profile")

    domain = email.split("@")[-1] if "@" in email else email
    tenant_name = domain.split(".")[0].capitalize()

    async with db.begin():
        tenant = await _get_or_create_tenant(db, domain, tenant_name)
        user = await _get_or_create_user(
            db, tenant.id, email, full_name, "microsoft", ms_sub
        )

    jwt_token = _create_access_token(user, tenant)

    redirect_url = (
        f"{settings.FRONTEND_URL}/auth/callback"
        f"?token={jwt_token}"
        f"&tenant_id={tenant.id}"
    )
    return RedirectResponse(url=redirect_url)


# ─────────────────────────────────────────────────────
# Google OAuth
# ─────────────────────────────────────────────────────


@router.get("/google/login")
async def google_login():
    """Redirect user to Google OAuth consent screen."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = "google"

    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={settings.FRONTEND_URL}/api/auth/google/callback"
        f"&scope=openid+email+profile"
        f"&state={state}"
        f"&access_type=offline"
    )
    return RedirectResponse(url=authorize_url)


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback."""
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    _oauth_states.pop(state, None)

    async with httpx.AsyncClient() as client:
        # Exchange authorization code for tokens
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "redirect_uri": f"{settings.FRONTEND_URL}/api/auth/google/callback",
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if token_response.status_code != 200:
            raise HTTPException(
                status_code=400, detail="Failed to exchange authorization code"
            )

        token_data = token_response.json()

    # Decode the id_token to get user claims (Google signs it — verify in prod)
    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="No id_token from Google")

    # Decode without verification for claim extraction
    # (In production, verify with Google's public keys)
    import base64
    import json as _json

    try:
        parts = id_token.split(".")
        # Add padding
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to decode id_token")

    email = claims.get("email", "")
    full_name = claims.get("name")
    google_sub = claims.get("sub", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in Google profile")

    domain = email.split("@")[-1] if "@" in email else email
    tenant_name = domain.split(".")[0].capitalize()

    async with db.begin():
        tenant = await _get_or_create_tenant(db, domain, tenant_name)
        user = await _get_or_create_user(
            db, tenant.id, email, full_name, "google", google_sub
        )

    jwt_token = _create_access_token(user, tenant)

    redirect_url = (
        f"{settings.FRONTEND_URL}/auth/callback"
        f"?token={jwt_token}"
        f"&tenant_id={tenant.id}"
    )
    return RedirectResponse(url=redirect_url)


# ─────────────────────────────────────────────────────
# Logout / Me
# ─────────────────────────────────────────────────────


@router.post("/logout")
async def logout():
    """Logout endpoint — client should delete its token."""
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserInfo)
async def get_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the currently authenticated user's info."""
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
