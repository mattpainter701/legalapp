import base64
import json as _json
import secrets
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.tenant import get_current_user
from app.models.tenant import Tenant
from app.models.user import User
from app.routers.billing import ensure_stripe_customer
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    OAuthCallbackExchangeRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserInfo,
)

settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# OAuth state TTL in seconds
_STATE_TTL = 600


def _state_key(state: str) -> str:
    return f"oauth:state:{state}"


def _state_data_key(state: str) -> str:
    return f"oauth:statedata:{state}"


async def _save_state(request: Request, state: str, data: dict = None) -> None:
    """Persist OAuth state token in Redis (falls back to in-process dict if Redis is absent)."""
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.setex(_state_key(state), _STATE_TTL, "1")
        if data:
            await redis.setex(_state_data_key(state), _STATE_TTL, _json.dumps(data))
    else:
        _gc_fallback_dicts()
        _fallback_states[state] = _time.time()
        if data:
            _fallback_state_data[state] = data


async def _consume_state(request: Request, state: str) -> tuple[bool, dict | None]:
    """Return (is_valid, signup_data_or_None)."""
    redis = getattr(request.app.state, "redis", None)
    data = None
    if redis:
        deleted = await redis.delete(_state_key(state))
        if deleted:
            raw = await redis.get(_state_data_key(state))
            if raw:
                data = _json.loads(raw)
            await redis.delete(_state_data_key(state))
        return bool(deleted), data
    ts = _fallback_states.pop(state, None)
    if ts is None:
        return False, None
    data = _fallback_state_data.pop(state, None)
    if _time.time() - ts > _FALLBACK_TTL:
        return False, None
    return True, data


_FALLBACK_TTL = 600
_CALLBACK_CODE_TTL = 60

_fallback_states: dict[str, float] = {}
_fallback_state_data: dict[str, dict] = {}
_fallback_callback_tokens: dict[str, tuple[str, float]] = {}


def _gc_fallback_dicts() -> None:
    now = _time.time()
    for k, v in list(_fallback_states.items()):
        if now - v > _FALLBACK_TTL:
            del _fallback_states[k]
            _fallback_state_data.pop(k, None)
    for k, v in list(_fallback_reset_tokens.items()):
        if now - v[1] > _FALLBACK_TTL:
            del _fallback_reset_tokens[k]
    for k, v in list(_fallback_callback_tokens.items()):
        if now - v[1] > _CALLBACK_CODE_TTL:
            del _fallback_callback_tokens[k]


def _callback_code_key(code: str) -> str:
    return f"oauth:callback:{code}"


async def _save_callback_token(request: Request, token: str) -> str:
    code = secrets.token_urlsafe(32)
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.setex(_callback_code_key(code), _CALLBACK_CODE_TTL, token)
    else:
        _gc_fallback_dicts()
        _fallback_callback_tokens[code] = (token, _time.time())
    return code


async def _consume_callback_token(request: Request, code: str) -> str | None:
    redis = getattr(request.app.state, "redis", None)
    if redis:
        token = await redis.get(_callback_code_key(code))
        await redis.delete(_callback_code_key(code))
        if isinstance(token, bytes):
            return token.decode("utf-8")
        return token
    entry = _fallback_callback_tokens.pop(code, None)
    if not entry:
        return None
    token, ts = entry
    if _time.time() - ts > _CALLBACK_CODE_TTL:
        return None
    return token


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# ── Token helpers ──────────────────────────────────────────────────────────────


def _create_access_token(user: User, tenant: Tenant) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": user.email,
        "billing_tier": tenant.billing_tier,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
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
    allow_create: bool = True,
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
            if not allow_create:
                raise HTTPException(
                    status_code=403,
                    detail="An administrator must invite this account before it can join the tenant",
                )
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


def _oauth_configured(client_id: str, client_secret: str) -> bool:
    """Reject empty, whitespace-only, or obviously bogus placeholder values."""
    cid = (client_id or "").strip()
    cs = (client_secret or "").strip()
    if not cid or not cs:
        return False
    if cid.startswith("#") or "TODO" in cid.upper():
        return False
    return True


@router.get("/microsoft/login")
async def microsoft_login(
    request: Request,
    signup: str = "",
    company_name: str = "",
    address: str = "",
    phone: str = "",
    staff_size: str = "",
):
    if not _oauth_configured(
        settings.MICROSOFT_CLIENT_ID, settings.MICROSOFT_CLIENT_SECRET
    ):
        raise HTTPException(status_code=501, detail="Microsoft OAuth not configured")

    state = secrets.token_urlsafe(32)
    signup_data = None
    if signup == "true":
        signup_data = {
            "company_name": company_name,
            "address": address,
            "phone": phone,
            "staff_size": staff_size,
        }
    await _save_state(request, state, signup_data)

    ms_tenant = settings.MICROSOFT_TENANT_ID
    redirect_uri = f"{settings.BACKEND_URL}/api/auth/microsoft/callback"

    authorize_url = (
        f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/authorize"
        f"?client_id={settings.MICROSOFT_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope=openid+email+profile+User.Read+offline_access"
        f"&state={state}"
        f"&response_mode=query"
        f"&prompt=select_account"
    )
    return RedirectResponse(url=authorize_url)


@router.get("/microsoft/callback")
async def microsoft_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, signup_data = await _consume_state(request, state)
    if not valid:
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
                "scope": "openid email profile User.Read offline_access",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if token_response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail="Failed to exchange Microsoft authorization code",
            )

        token_data = token_response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=400, detail="No access token from Microsoft"
            )

        graph_response = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if graph_response.status_code != 200:
            raise HTTPException(
                status_code=400, detail="Failed to fetch Microsoft profile"
            )

        profile = graph_response.json()

    email = (
        (profile.get("mail") or profile.get("userPrincipalName") or "").lower().strip()
    )
    full_name = profile.get("displayName")
    ms_sub = profile.get("id", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in Microsoft profile")

    domain = email.split("@")[-1]
    # Use provided company info from signup flow, or derive from email domain
    if signup_data and signup_data.get("company_name"):
        tenant_name = signup_data["company_name"]
        company_name = signup_data.get("company_name")
        address = signup_data.get("address")
        phone = signup_data.get("phone")
        staff_size_str = signup_data.get("staff_size", "")
        try:
            staff_size = int(staff_size_str) if staff_size_str else None
        except (ValueError, TypeError):
            staff_size = None
    else:
        tenant_name = domain.split(".")[0].capitalize()
        company_name = None
        address = None
        phone = None
        staff_size = None

    async with db.begin():
        tenant_exists = False
        if signup_data and signup_data.get("company_name"):
            result = await db.execute(select(Tenant).where(Tenant.domain == domain))
            tenant = result.scalar_one_or_none()
            tenant_exists = tenant is not None
            if tenant is None:
                tenant = Tenant(
                    id=uuid.uuid4(),
                    name=tenant_name,
                    domain=domain,
                    company_name=company_name,
                    address=address,
                    phone=phone,
                    staff_size=staff_size,
                    billing_tier="payg",
                    is_active=True,
                )
                db.add(tenant)
                await db.flush()
            user = await _get_or_create_user(
                db,
                tenant.id,
                email,
                full_name,
                "microsoft",
                ms_sub,
                allow_create=not tenant_exists,
            )
        else:
            result = await db.execute(select(Tenant).where(Tenant.domain == domain))
            tenant = result.scalar_one_or_none()
            tenant_exists = tenant is not None
            if tenant is None:
                tenant = await _get_or_create_tenant(db, domain, tenant_name)
            user = await _get_or_create_user(
                db,
                tenant.id,
                email,
                full_name,
                "microsoft",
                ms_sub,
                allow_create=not tenant_exists,
            )
        await ensure_stripe_customer(tenant, db)

    jwt_token = _create_access_token(user, tenant)
    callback_code = await _save_callback_token(request, jwt_token)
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback?code={callback_code}"
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

    from jose import jwk

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
        raise HTTPException(
            status_code=400, detail="No matching Google public key for token kid"
        )

    try:
        public_key = jwk.construct(matching_key)
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_CLIENT_ID,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Google id_token verification failed: {exc}"
        )

    return claims


@router.get("/google/login")
async def google_login(
    request: Request,
    signup: str = "",
    company_name: str = "",
    address: str = "",
    phone: str = "",
    staff_size: str = "",
):
    if not _oauth_configured(settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET):
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    state = secrets.token_urlsafe(32)
    signup_data = None
    if signup == "true":
        signup_data = {
            "company_name": company_name,
            "address": address,
            "phone": phone,
            "staff_size": staff_size,
        }
    await _save_state(request, state, signup_data)

    redirect_uri = f"{settings.BACKEND_URL}/api/auth/google/callback"

    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope=openid+email+profile"
        f"&state={state}"
        f"&access_type=offline"
        f"&prompt=consent"
    )
    return RedirectResponse(url=authorize_url)


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, signup_data = await _consume_state(request, state)
    if not valid:
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
            raise HTTPException(
                status_code=400, detail="Failed to exchange Google authorization code"
            )

        token_data = token_response.json()

    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="No id_token from Google")

    claims = await _verify_google_id_token(id_token)
    if claims.get("email_verified") is not True:
        raise HTTPException(status_code=400, detail="Google email is not verified")

    email = claims.get("email", "").lower().strip()
    full_name = claims.get("name")
    google_sub = claims.get("sub", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in Google profile")

    domain = email.split("@")[-1]
    # Use provided company info from signup flow, or derive from email domain
    if signup_data and signup_data.get("company_name"):
        tenant_name = signup_data["company_name"]
        company_name = signup_data.get("company_name")
        address = signup_data.get("address")
        phone = signup_data.get("phone")
        staff_size_str = signup_data.get("staff_size", "")
        try:
            staff_size = int(staff_size_str) if staff_size_str else None
        except (ValueError, TypeError):
            staff_size = None
    else:
        tenant_name = domain.split(".")[0].capitalize()
        company_name = None
        address = None
        phone = None
        staff_size = None

    async with db.begin():
        tenant_exists = False
        if signup_data and signup_data.get("company_name"):
            result = await db.execute(select(Tenant).where(Tenant.domain == domain))
            tenant = result.scalar_one_or_none()
            tenant_exists = tenant is not None
            if tenant is None:
                tenant = Tenant(
                    id=uuid.uuid4(),
                    name=tenant_name,
                    domain=domain,
                    company_name=company_name,
                    address=address,
                    phone=phone,
                    staff_size=staff_size,
                    billing_tier="payg",
                    is_active=True,
                )
                db.add(tenant)
                await db.flush()
            user = await _get_or_create_user(
                db,
                tenant.id,
                email,
                full_name,
                "google",
                google_sub,
                allow_create=not tenant_exists,
            )
        else:
            result = await db.execute(select(Tenant).where(Tenant.domain == domain))
            tenant = result.scalar_one_or_none()
            tenant_exists = tenant is not None
            if tenant is None:
                tenant = await _get_or_create_tenant(db, domain, tenant_name)
            user = await _get_or_create_user(
                db,
                tenant.id,
                email,
                full_name,
                "google",
                google_sub,
                allow_create=not tenant_exists,
            )
        await ensure_stripe_customer(tenant, db)

    jwt_token = _create_access_token(user, tenant)
    callback_code = await _save_callback_token(request, jwt_token)
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback?code={callback_code}"
    )


@router.post("/oauth/exchange", response_model=TokenResponse)
async def exchange_oauth_callback(
    body: OAuthCallbackExchangeRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    token = await _consume_callback_token(request, body.code)
    if not token:
        raise HTTPException(
            status_code=400, detail="Invalid or expired OAuth callback code"
        )

    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid OAuth callback token")

    result = await db.execute(select(User).where(User.id == payload.get("sub")))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Set httpOnly cookie with secure flags
    # Only set Secure in production (https://...) — dev uses http://localhost
    is_production = settings.BACKEND_URL.startswith("https://")
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=is_production,
        samesite="Lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    return TokenResponse(
        access_token=token,
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        role=user.role,
        email=user.email,
        full_name=user.full_name,
    )


# ── Email/password register & login ────────────────────────────────────────────


@router.post("/register")
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body.email = body.email.lower().strip()

    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    domain = body.email.split("@")[-1]
    tenant_name = body.company_name or domain.split(".")[0].capitalize()

    tenant_result = await db.execute(select(Tenant).where(Tenant.domain == domain))
    tenant = tenant_result.scalar_one_or_none()

    if tenant is None:
        tenant = Tenant(
            id=uuid.uuid4(),
            name=tenant_name,
            domain=domain,
            company_name=body.company_name,
            staff_size=body.staff_size,
            address=body.address,
            phone=body.phone,
            billing_tier="payg",
            is_active=True,
        )
        db.add(tenant)
        await db.flush()
        role = "admin"
    else:
        raise HTTPException(
            status_code=403,
            detail="An administrator must invite this account before it can join the tenant",
        )

    password_hash = _hash_password(body.password)

    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=body.email,
        full_name=body.full_name or "",
        password_hash=password_hash,
        role=role,
        is_active=True,
    )
    db.add(user)
    await ensure_stripe_customer(tenant, db)
    await db.commit()
    await db.refresh(user)
    await db.refresh(tenant)

    jwt_token = _create_access_token(user, tenant)
    return TokenResponse(
        access_token=jwt_token,
        user_id=str(user.id),
        tenant_id=str(tenant.id),
        role=user.role,
        email=user.email,
        full_name=user.full_name,
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    body.email = body.email.lower().strip()

    result = await db.execute(
        select(User)
        .where(User.email == body.email)
        .order_by(User.created_at.desc())
        .limit(1)
    )
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    tenant = user.tenant
    jwt_token = _create_access_token(user, tenant)

    # Set httpOnly cookie with secure flags
    # Only set Secure in production (https://...) — dev uses http://localhost
    is_production = settings.BACKEND_URL.startswith("https://")
    response.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        secure=is_production,
        samesite="Lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    # For backward compatibility, still return token in body (but frontend will prefer cookie)
    return TokenResponse(
        access_token=jwt_token,
        user_id=str(user.id),
        tenant_id=str(tenant.id),
        role=user.role,
        email=user.email,
        full_name=user.full_name,
    )


# ── Password reset ─────────────────────────────────────────────────────────────

_RESET_TTL = 1800  # 30 minutes


def _reset_key(token: str) -> str:
    return f"reset:token:{token}"


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body.email = body.email.lower().strip()

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # Always return success to avoid email enumeration
    if not user or not user.password_hash:
        return {
            "message": "If that email exists, a reset link has been sent.",
            "reset_token": None,
        }

    token = secrets.token_urlsafe(32)
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.setex(_reset_key(token), _RESET_TTL, user.email)
    else:
        _gc_fallback_dicts()
        _fallback_reset_tokens[token] = (user.email, _time.time())

    if settings.EMAIL_ENABLED:
        # TODO: send reset email with FRONTEND_URL/reset-password?token=<token>
        return {"message": "If that email exists, a reset link has been sent."}
    else:
        return {
            "message": "If that email exists, a reset link has been sent.",
            "reset_token": token if settings.DEV_MODE else None,
        }


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    redis = getattr(request.app.state, "redis", None)
    email = None

    if redis:
        email_bytes = await redis.get(_reset_key(body.token))
        if email_bytes:
            email = email_bytes.decode("utf-8")
            await redis.delete(_reset_key(body.token))
    else:
        entry = _fallback_reset_tokens.pop(body.token, None)
        if entry:
            email, ts = entry
            if _time.time() - ts > _RESET_TTL:
                email = None

    if not email:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.password_hash = _hash_password(body.password)
    await db.commit()

    return {"message": "Password reset successfully"}


_fallback_reset_tokens: dict[str, tuple[str, float]] = {}


# ── Logout / Me ────────────────────────────────────────────────────────────────


@router.post("/logout")
async def logout(request: Request, response: Response):
    # Extract token from cookie (preferred) or Authorization header (fallback)
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if token:
        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )
            jti = payload.get("jti")
            exp = payload.get("exp", 0)
            if jti and exp:
                ttl = max(0, exp - int(_time.time()))
                redis = getattr(request.app.state, "redis", None)
                if redis:
                    await redis.setex(f"jti:{jti}", ttl, "1")
                else:
                    request.app.state.jti_blacklist[jti] = _time.time() + ttl
        except Exception:
            pass

    # Clear the httpOnly cookie
    response.delete_cookie("access_token", httponly=True, samesite="Lax")

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
