import asyncio
import hashlib
import json as _json
import logging
import secrets
import time as _time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

import bcrypt
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from jose import jwt
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import enable_rls_bypass, get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.services.module_visibility import resolve_enabled_modules, resolve_plan_meta
from app.services.office_access import (
    require_office_globally_enabled,
    require_office_pilot_tenant,
)
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.user_oauth_token import UserOAuthToken
from app.models.user import User
from app.models.demo_session import DemoSession
from app.routers.billing import ensure_stripe_customer
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    OAuthCallbackExchangeRequest,
    PlanSignupRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserInfo,
    UserProfileUpdate,
)
from app.services.email import email_service
from app.utils.oauth_security import (
    generate_nonce,
    generate_pkce_pair,
    is_oauth_client_configured,
    verify_google_id_token,
    verify_microsoft_id_token,
    verify_microsoft_access_token,
)
from app.services.tenant_state import require_active_tenant

settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

CALENDAR_REQUIRED_SCOPES = {
    "microsoft": {"Calendars.ReadWrite"},
    "google": {"https://www.googleapis.com/auth/calendar"},
}

# OAuth state TTL in seconds
_STATE_TTL = 600


async def _active_demo_session(
    db: AsyncSession, tenant_id: uuid.UUID
) -> DemoSession | None:
    """Return optional demo metadata without making normal authentication depend on it.

    A signed-in user's profile is required to finish OAuth and load the workspace.
    Demo quota is only a display/control enhancement, so an incomplete demo
    migration or its RLS policy must not convert ``/auth/me`` into a 500.
    """
    try:
        return await db.scalar(
            select(DemoSession).where(
                DemoSession.tenant_id == tenant_id,
                DemoSession.status == "active",
            )
        )
    except Exception:
        logger.exception(
            "Unable to load optional demo metadata for tenant_id=%s", tenant_id
        )
        return None


def _require_public_signup_enabled() -> None:
    if not settings.PUBLIC_SIGNUP_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Public signup is not enabled; request access from the operator",
        )


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
    """Return (is_valid, state_meta_or_None) where state_meta holds signup/nonce/pkce_verifier."""
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
_CALLBACK_REPLAY_TTL = 60

_fallback_states: dict[str, float] = {}
_fallback_state_data: dict[str, dict] = {}
_fallback_callback_tokens: dict[str, tuple[str, float]] = {}
_fallback_callback_replays: dict[str, tuple[str, float]] = {}


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
    for k, v in list(_fallback_callback_replays.items()):
        if now - v[1] > _CALLBACK_REPLAY_TTL:
            del _fallback_callback_replays[k]


def _callback_code_key(code: str) -> str:
    return f"oauth:callback:{code}"


def _callback_replay_key(state: str, provider_code: str) -> str:
    digest = hashlib.sha256(f"{state}:{provider_code}".encode("utf-8")).hexdigest()
    return f"oauth:callbackreplay:{digest}"


async def _save_callback_token(request: Request, token: str) -> str:
    code = secrets.token_urlsafe(32)
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.setex(_callback_code_key(code), _CALLBACK_CODE_TTL, token)
    else:
        _gc_fallback_dicts()
        _fallback_callback_tokens[code] = (token, _time.time())
    return code


async def _save_callback_replay(
    request: Request, state: str, provider_code: str, token: str
) -> None:
    redis = getattr(request.app.state, "redis", None)
    key = _callback_replay_key(state, provider_code)
    if redis:
        await redis.setex(key, _CALLBACK_REPLAY_TTL, token)
    else:
        _gc_fallback_dicts()
        _fallback_callback_replays[key] = (token, _time.time())


async def _get_callback_replay_token(
    request: Request, state: str, provider_code: str
) -> str | None:
    redis = getattr(request.app.state, "redis", None)
    key = _callback_replay_key(state, provider_code)
    if redis:
        token = await redis.get(key)
        if isinstance(token, bytes):
            return token.decode("utf-8")
        return token
    entry = _fallback_callback_replays.get(key)
    if not entry:
        return None
    token, ts = entry
    if _time.time() - ts > _CALLBACK_REPLAY_TTL:
        _fallback_callback_replays.pop(key, None)
        return None
    return token


def _frontend_callback_response(callback_code: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback?code={callback_code}"
    )


async def _replay_frontend_callback(
    request: Request, state: str, provider_code: str
) -> RedirectResponse | None:
    token = await _get_callback_replay_token(request, state, provider_code)
    if not token:
        return None
    callback_code = await _save_callback_token(request, token)
    logger.info("Replayed recently completed OAuth callback")
    return _frontend_callback_response(callback_code)


async def _wait_for_replayed_frontend_callback(
    request: Request, state: str, provider_code: str
) -> RedirectResponse | None:
    for _ in range(20):
        replay = await _replay_frontend_callback(request, state, provider_code)
        if replay:
            return replay
        await asyncio.sleep(0.1)
    return None


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


def _create_access_token(
    user: User,
    tenant: Tenant,
    plan_id: str = "full-platform",
    caps: list[str] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "caps": caps or [],
        "email": user.email,
        "billing_tier": tenant.billing_tier,
        "plan": plan_id,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def _issue_access_token(db: AsyncSession, user: User, tenant: Tenant) -> str:
    """Resolve the tenant's plan + user capabilities and mint an access token."""
    require_active_tenant(tenant)
    from app.services.module_visibility import resolve_plan_meta
    from app.services.rbac_service import get_user_capabilities

    # Auth paths (OAuth callbacks, login) run _issue_access_token after their
    # transaction commits, which resets all SET LOCAL GUCs including
    # app.current_tenant_id back to '' — causing the RLS policy on user_roles
    # to throw "invalid input syntax for type uuid: ''". Bind the tenant context
    # here so the user_roles query always runs with the correct RLS context.
    await set_tenant_context(db, str(user.tenant_id))
    plan_id, _ = await resolve_plan_meta(db, user.tenant_id)
    caps = sorted(await get_user_capabilities(db, user.id))
    return _create_access_token(user, tenant, plan_id, caps)


# ── Cookie + refresh-token helpers ─────────────────────────────────────────────


def _cookie_flags() -> dict:
    """Return consistent cookie security flags for all auth cookies.

    ``secure`` is taken from ``COOKIE_SECURE`` if explicitly set, otherwise
    derived from whether ``BACKEND_URL`` is https. ``samesite`` is normalised to
    the capitalised form Starlette expects ("Lax"/"Strict"/"None"). Per the spec,
    ``SameSite=None`` requires ``Secure``, so we force ``secure=True`` in that case.
    """
    if settings.COOKIE_SECURE is not None:
        secure = settings.COOKIE_SECURE
    else:
        secure = settings.BACKEND_URL.startswith("https://")
    samesite = settings.COOKIE_SAMESITE.capitalize()
    if samesite == "None":
        # SameSite=None is only valid alongside Secure.
        secure = True
    return {"secure": secure, "samesite": samesite}


def _refresh_key(token: str) -> str:
    return f"refresh:{token}"


def _refresh_family_key(family: str) -> str:
    return f"refresh_family:{family}"


def _refresh_used_key(token: str) -> str:
    return f"refresh_used:{token}"


def _refresh_family_revoked_key(family: str) -> str:
    return f"refresh_family_revoked:{family}"


_REFRESH_TTL = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400

# Consuming a token and writing its family tombstone must be one Redis operation.
# Otherwise two simultaneous refreshes can both observe the token as live before
# either deletes it. The tombstone inherits the token's *remaining* TTL, so replay
# detection lasts exactly as long as the consumed credential would have remained
# valid and storage cannot grow without an expiry bound.
_CONSUME_REFRESH_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if raw then
  local ttl_ms = redis.call('PTTL', KEYS[1])
  local payload = cjson.decode(raw)
  local family = payload['family']
  redis.call('DEL', KEYS[1])
  if family then
    if ttl_ms == -1 then
      ttl_ms = tonumber(ARGV[1]) * 1000
    end
    if ttl_ms < 1 then
      ttl_ms = 1
    end
    redis.call('PSETEX', KEYS[2], ttl_ms, family)
  end
  return {'consumed', raw}
end

local used_family = redis.call('GET', KEYS[2])
if used_family then
  return {'replay', used_family}
end
return {'missing', ''}
"""

# Issuance and the revoked-family check are atomic. This closes the race where a
# replay revokes a family while the winning request is still minting its successor.
_ISSUE_REFRESH_SCRIPT = """
if redis.call('EXISTS', KEYS[3]) == 1 then
  return 0
end
redis.call('SETEX', KEYS[1], ARGV[1], ARGV[2])
redis.call('SADD', KEYS[2], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[1])
return 1
"""

# Marking a family revoked and deleting every currently-live member are atomic
# with respect to the issuance script above: either issuance wins first and its
# token is deleted, or revocation wins first and issuance is refused.
_REVOKE_REFRESH_FAMILY_SCRIPT = """
redis.call('SETEX', KEYS[2], ARGV[1], '1')
local members = redis.call('SMEMBERS', KEYS[1])
for _, token in ipairs(members) do
  redis.call('DEL', ARGV[2] .. token)
end
redis.call('DEL', KEYS[1])
return #members
"""


def _redis_text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


async def _consume_refresh_token(request: Request, token: str) -> tuple[str, str]:
    """Atomically consume a live token or recover its family after replay."""

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return "missing", ""
    result = await redis.eval(
        _CONSUME_REFRESH_SCRIPT,
        2,
        _refresh_key(token),
        _refresh_used_key(token),
        _REFRESH_TTL,
    )
    status = _redis_text(result[0])
    value = _redis_text(result[1])
    if status == "consumed":
        data = _json.loads(value)
        family = data.get("family")
        if family:
            # This set deliberately contains live tokens only. Consumed-token
            # tombstones expire independently at their original expiry time.
            await redis.srem(_refresh_family_key(family), token)
    return status, value


async def _create_refresh_token(
    request: Request, user: User, family: str | None = None
) -> str:
    """Mint a new opaque refresh token, persisted in Redis and tracked by family.

    The ``family`` identifies a rotation chain; when omitted a fresh uuid family
    is created (issued at login / register / oauth). On rotation the caller
    passes the existing family so the new token joins the same chain — which lets
    us revoke the whole chain on token-reuse detection.

    Redis layout:
      ``refresh:{token}``         -> JSON {"user_id", "family"}, TTL = REFRESH_TTL
      ``refresh_family:{family}`` -> SET of live tokens in the chain, TTL = REFRESH_TTL
      ``refresh_used:{token}``    -> family id, TTL = consumed token's remaining TTL
      ``refresh_family_revoked:*`` prevents a revoked chain from being reissued

    If Redis is unavailable we still return a token, but it is NOT persisted, so
    ``/auth/refresh`` will reject it. Refresh is therefore effectively disabled
    in dev without Redis — logged loudly so it is not mistaken for a prod config.
    """
    token = secrets.token_urlsafe(48)
    if family is None:
        family = str(uuid.uuid4())

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        logger.warning(
            "Redis unavailable: refresh token NOT persisted; /auth/refresh will "
            "reject it. Refresh-token rotation is disabled (dev-only)."
        )
        return token

    payload = _json.dumps({"user_id": str(user.id), "family": family})
    issued = await redis.eval(
        _ISSUE_REFRESH_SCRIPT,
        3,
        _refresh_key(token),
        _refresh_family_key(family),
        _refresh_family_revoked_key(family),
        _REFRESH_TTL,
        payload,
        token,
    )
    if not issued:
        raise HTTPException(status_code=401, detail="Refresh token family revoked")
    return token


async def _revoke_refresh_family(request: Request, family: str) -> None:
    """Atomically mark a rotation chain revoked and delete every live token.

    Consumed-token tombstones remain only until each token's original expiry;
    they contain no usable credential and let stale replays identify this family.
    The family revocation marker lasts one full token lifetime, which prevents a
    concurrent request from resurrecting the chain after its live set is deleted.
    """
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    await redis.eval(
        _REVOKE_REFRESH_FAMILY_SCRIPT,
        2,
        _refresh_family_key(family),
        _refresh_family_revoked_key(family),
        _REFRESH_TTL,
        _refresh_key(""),
    )


def _set_auth_cookies(
    response: Response, access_token: str, refresh_token: str
) -> None:
    """Set both the access and refresh cookies with consistent, hardened flags."""
    flags = _cookie_flags()
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        **flags,
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=_REFRESH_TTL,
        path="/",
        **flags,
    )


# ── Tenant / user upsert helpers ───────────────────────────────────────────────


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
                func.lower(User.email) == email.lower(),
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


async def _resolve_existing_oauth_user(
    db: AsyncSession,
    *,
    email: str,
    provider: str,
    subject: str,
    allow_verified_email_match: bool,
) -> User | None:
    """Resolve an already-provisioned OAuth identity without guessing a tenant.

    Provider subject is the strongest mapping and therefore wins when it is
    uniquely linked. Google may additionally use a unique, case-insensitive
    email after its ``email_verified`` claim has been enforced by the callback.
    Microsoft email/UPN claims are not an authorization boundary, so Microsoft
    callers always set ``allow_verified_email_match=False``. Ambiguous mappings
    fail closed instead of selecting an arbitrary tenant.
    """

    async def _unique_match(statement, mapping: str) -> User | None:
        result = await db.execute(statement.limit(2))
        matches = list(result.scalars().all())
        if len(matches) > 1:
            logger.error(
                "Ambiguous OAuth %s mapping rejected for provider=%s",
                mapping,
                provider,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "This sign-in identity matches multiple accounts; "
                    "contact the operator to resolve the account mapping"
                ),
            )
        return matches[0] if matches else None

    if subject:
        subject_match = await _unique_match(
            select(User).where(
                User.oauth_provider == provider,
                User.oauth_subject == subject,
            ),
            "provider-subject",
        )
        if subject_match is not None:
            return subject_match

    if not allow_verified_email_match:
        return None

    return await _unique_match(
        select(User).where(func.lower(User.email) == email.lower()),
        "email",
    )


async def _resolve_oauth_tenant_and_user(
    db: AsyncSession,
    *,
    email: str,
    full_name: str | None,
    provider: str,
    subject: str,
    domain: str,
    tenant_name: str,
    company_name: str | None = None,
    address: str | None = None,
    phone: str | None = None,
    staff_size: int | None = None,
) -> tuple[Tenant, User, bool]:
    """Map a verified OAuth identity, provisioning only when explicitly enabled.

    Returns ``(tenant, user, tenant_existed)``.  Existing identities are
    resolved before any domain-based tenant lookup so login remains functional
    for synthetic tenant domains, established subject links, and verified
    Google-email invitees.
    """

    existing_user = await _resolve_existing_oauth_user(
        db,
        email=email,
        provider=provider,
        subject=subject,
        allow_verified_email_match=provider == "google",
    )
    if existing_user is not None:
        tenant_result = await db.execute(
            select(Tenant).where(Tenant.id == existing_user.tenant_id)
        )
        tenant = tenant_result.scalar_one_or_none()
        if tenant is None:
            logger.error(
                "OAuth user references a missing tenant: provider=%s user_id=%s",
                provider,
                existing_user.id,
            )
            raise HTTPException(status_code=403, detail="Account tenant is unavailable")

        require_active_tenant(tenant)

        user = await _get_or_create_user(
            db,
            tenant.id,
            email,
            full_name,
            provider,
            subject,
            allow_create=False,
        )
        return tenant, user, True

    if provider == "microsoft":
        raise HTTPException(
            status_code=403,
            detail=(
                "This Microsoft account is not linked to an existing user; "
                "sign in with your established method and ask the operator to "
                "link the Microsoft account"
            ),
        )

    tenant_result = await db.execute(select(Tenant).where(Tenant.domain == domain))
    tenant = tenant_result.scalar_one_or_none()
    tenant_existed = tenant is not None
    if tenant is None:
        _require_public_signup_enabled()
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
    else:
        require_active_tenant(tenant)

    user = await _get_or_create_user(
        db,
        tenant.id,
        email,
        full_name,
        provider,
        subject,
        allow_create=not tenant_existed,
    )
    return tenant, user, tenant_existed


# ── Microsoft OAuth ────────────────────────────────────────────────────────────


_oauth_configured = is_oauth_client_configured


@router.get("/microsoft/login")
async def microsoft_login(
    request: Request,
    signup: str = "",
    company_name: str = "",
    address: str = "",
    phone: str = "",
    staff_size: str = "",
):
    if signup == "true":
        _require_public_signup_enabled()
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
    nonce = generate_nonce()
    code_verifier, code_challenge = generate_pkce_pair()
    await _save_state(
        request,
        state,
        {"signup": signup_data, "nonce": nonce, "pkce_verifier": code_verifier},
    )

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
        f"&nonce={nonce}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )
    response = RedirectResponse(url=authorize_url)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/microsoft/callback")
async def microsoft_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, state_meta = await _consume_state(request, state)
    if not valid:
        replay = await _wait_for_replayed_frontend_callback(request, state, code)
        if replay:
            return replay
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    state_meta = state_meta or {}
    signup_data = state_meta.get("signup")
    expected_nonce = state_meta.get("nonce")
    code_verifier = state_meta.get("pkce_verifier")

    redirect_uri = f"{settings.BACKEND_URL}/api/auth/microsoft/callback"
    ms_tenant = settings.MICROSOFT_TENANT_ID
    token_url = f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/token"

    token_data_payload = {
        "client_id": settings.MICROSOFT_CLIENT_ID,
        "client_secret": settings.MICROSOFT_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "scope": "openid email profile User.Read offline_access",
    }
    if code_verifier:
        token_data_payload["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=30.0) as client:
        token_response = await client.post(
            token_url,
            data=token_data_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if token_response.status_code != 200:
            logger.error(
                "Microsoft token exchange failed: status=%d body=%s",
                token_response.status_code,
                token_response.text,
            )
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

        # Extract user profile from id_token (always available, no Graph call needed)
        id_token_raw = token_data.get("id_token")
        if not id_token_raw:
            logger.error("Microsoft token response missing id_token")
            raise HTTPException(
                status_code=400,
                detail="No id_token in Microsoft token response",
            )

        claims = await verify_microsoft_id_token(
            id_token_raw,
            client_id=settings.MICROSOFT_CLIENT_ID,
            tenant=ms_tenant,
            expected_nonce=expected_nonce,
        )

        logger.info(
            "Microsoft id_token claims: sub=%s email=%s name=%s",
            claims.get("sub"),
            claims.get("email"),
            claims.get("name"),
        )

    email = (
        (claims.get("email") or claims.get("preferred_username") or "").lower().strip()
    )
    full_name = claims.get("name")
    ms_sub = claims.get("sub") or claims.get("oid", "")

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
        # OAuth identity matching is intentionally cross-tenant: resolve an
        # already-provisioned user before considering domain-based provisioning.
        await enable_rls_bypass(db)
        tenant, user, tenant_exists = await _resolve_oauth_tenant_and_user(
            db,
            email=email,
            full_name=full_name,
            provider="microsoft",
            subject=ms_sub,
            domain=domain,
            tenant_name=tenant_name,
            company_name=company_name,
            address=address,
            phone=phone,
            staff_size=staff_size,
        )
        await ensure_stripe_customer(tenant, db)

        # New firm: the first user of a brand-new tenant is created as admin by
        # _get_or_create_user. Seed system roles + assign Administrator so the
        # minted JWT carries manage_roles/admin_settings caps. Inside db.begin()
        # so provision flushes only; the transaction block commits on exit.
        if not tenant_exists:
            from app.services.rbac_service import provision_tenant_rbac

            await provision_tenant_rbac(db, tenant.id, user.id)

    jwt_token = await _issue_access_token(db, user, tenant)
    await _save_callback_replay(request, state, code, jwt_token)
    callback_code = await _save_callback_token(request, jwt_token)
    return _frontend_callback_response(callback_code)


# ── Google OAuth ───────────────────────────────────────────────────────────────


@router.get("/google/login")
async def google_login(
    request: Request,
    signup: str = "",
    company_name: str = "",
    address: str = "",
    phone: str = "",
    staff_size: str = "",
):
    if signup == "true":
        _require_public_signup_enabled()
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
    nonce = generate_nonce()
    code_verifier, code_challenge = generate_pkce_pair()
    await _save_state(
        request,
        state,
        {"signup": signup_data, "nonce": nonce, "pkce_verifier": code_verifier},
    )

    redirect_uri = f"{settings.BACKEND_URL}/api/auth/google/callback"
    encoded_redirect = urllib.parse.quote(redirect_uri, safe="")

    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={encoded_redirect}"
        f"&scope=openid+email+profile"
        f"&state={state}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&nonce={nonce}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )
    response = RedirectResponse(url=authorize_url)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, state_meta = await _consume_state(request, state)
    if not valid:
        replay = await _wait_for_replayed_frontend_callback(request, state, code)
        if replay:
            return replay
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    state_meta = state_meta or {}
    signup_data = state_meta.get("signup")
    expected_nonce = state_meta.get("nonce")
    code_verifier = state_meta.get("pkce_verifier")

    redirect_uri = f"{settings.BACKEND_URL}/api/auth/google/callback"

    token_data_payload = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        token_data_payload["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=30.0) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data=token_data_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if token_response.status_code != 200:
            logger.error(
                "Google token exchange failed: status=%d body=%s",
                token_response.status_code,
                token_response.text,
            )
            raise HTTPException(
                status_code=400, detail="Failed to exchange Google authorization code"
            )

        token_data = token_response.json()

    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="No id_token from Google")

    claims = await verify_google_id_token(
        id_token,
        client_id=settings.GOOGLE_CLIENT_ID,
        expected_nonce=expected_nonce,
        access_token=token_data.get("access_token"),
    )
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
        # OAuth identity matching is intentionally cross-tenant: resolve an
        # already-provisioned user before considering domain-based provisioning.
        await enable_rls_bypass(db)
        tenant, user, tenant_exists = await _resolve_oauth_tenant_and_user(
            db,
            email=email,
            full_name=full_name,
            provider="google",
            subject=google_sub,
            domain=domain,
            tenant_name=tenant_name,
            company_name=company_name,
            address=address,
            phone=phone,
            staff_size=staff_size,
        )
        await ensure_stripe_customer(tenant, db)

        # New firm: the first user of a brand-new tenant is created as admin by
        # _get_or_create_user. Seed system roles + assign Administrator so the
        # minted JWT carries manage_roles/admin_settings caps. Inside db.begin()
        # so provision flushes only; the transaction block commits on exit.
        if not tenant_exists:
            from app.services.rbac_service import provision_tenant_rbac

            await provision_tenant_rbac(db, tenant.id, user.id)

    jwt_token = await _issue_access_token(db, user, tenant)
    await _save_callback_replay(request, state, code, jwt_token)
    callback_code = await _save_callback_token(request, jwt_token)
    return _frontend_callback_response(callback_code)


@router.post("/office/exchange", response_model=TokenResponse)
async def exchange_office_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Exchange one verified Office NAA token for hardened LawHand cookies.

    This endpoint never provisions a tenant or user. It links only an existing
    Microsoft OAuth identity, then persists the immutable Entra tenant/object
    pair so later exchanges no longer depend on pairwise subject behavior.
    """

    require_office_globally_enabled()

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Microsoft access token required")
    access_token = auth_header.split(" ", 1)[1].strip()
    if not access_token:
        raise HTTPException(status_code=401, detail="Microsoft access token required")

    claims = await verify_microsoft_access_token(
        access_token,
        audience=settings.OFFICE_ENTRA_API_AUDIENCE,
        required_scope=settings.OFFICE_ENTRA_REQUIRED_SCOPE,
        client_id=settings.OFFICE_ENTRA_CLIENT_ID or settings.MICROSOFT_CLIENT_ID,
        tenant=settings.MICROSOFT_TENANT_ID,
    )
    entra_tenant_id = claims["tid"]
    entra_object_id = claims["oid"]
    legacy_subjects = {value for value in (claims.get("sub"), entra_object_id) if value}

    await enable_rls_bypass(db)
    user_result = await db.execute(
        select(User)
        .options(selectinload(User.tenant))
        .where(
            or_(
                and_(
                    User.entra_tenant_id == entra_tenant_id,
                    User.entra_object_id == entra_object_id,
                ),
                and_(
                    User.oauth_provider == "microsoft",
                    User.oauth_subject.in_(legacy_subjects),
                ),
            )
        )
    )
    users = user_result.scalars().unique().all()
    if len(users) != 1:
        raise HTTPException(
            status_code=403,
            detail="Office identity is not linked to exactly one LawHand user",
        )

    user = users[0]
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")
    require_active_tenant(user.tenant)
    require_office_pilot_tenant(user.tenant_id)
    if user.entra_tenant_id and user.entra_tenant_id != entra_tenant_id:
        raise HTTPException(status_code=409, detail="Microsoft tenant link mismatch")
    if user.entra_object_id and user.entra_object_id != entra_object_id:
        raise HTTPException(status_code=409, detail="Microsoft object link mismatch")

    if not user.entra_tenant_id or not user.entra_object_id:
        user.entra_tenant_id = entra_tenant_id
        user.entra_object_id = entra_object_id
        await db.commit()

    jwt_token = await _issue_access_token(db, user, user.tenant)
    refresh_token = await _create_refresh_token(request, user)
    _set_auth_cookies(response, jwt_token, refresh_token)
    return TokenResponse(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        role=user.role,
        email=user.email,
        full_name=user.full_name,
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

    # Cross-tenant user-by-id lookup with no tenant context: allow RLS bypass.
    await enable_rls_bypass(db)
    result = await db.execute(select(User).where(User.id == payload.get("sub")))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    require_active_tenant(user.tenant)

    # Set hardened httpOnly access + refresh cookies.
    refresh_token = await _create_refresh_token(request, user)
    _set_auth_cookies(response, token, refresh_token)

    return TokenResponse(
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
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    _require_public_signup_enabled()
    body.email = body.email.lower().strip()

    # Cross-tenant email-exists check with no tenant context: allow RLS bypass.
    await enable_rls_bypass(db)
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
    await enable_rls_bypass(db)
    await db.refresh(user)
    await db.refresh(tenant)

    # New firm: seed system roles + assign the founding admin the Administrator
    # system role so the minted JWT carries manage_roles/admin_settings caps.
    from app.services.rbac_service import provision_tenant_rbac

    await provision_tenant_rbac(db, tenant.id, user.id)
    await db.commit()

    jwt_token = await _issue_access_token(db, user, tenant)
    refresh_token = await _create_refresh_token(request, user)
    _set_auth_cookies(response, jwt_token, refresh_token)
    return TokenResponse(
        user_id=str(user.id),
        tenant_id=str(tenant.id),
        role=user.role,
        email=user.email,
        full_name=user.full_name,
    )


@router.post("/signup/plan", status_code=201)
async def signup_with_plan(
    body: PlanSignupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Self-serve provisioning for a public plan (e.g. standalone Call Intake).

    Creates a tenant on the plan's billing tier, an admin user, and a trial
    window. Only plans flagged ``public_signup`` may be requested here.
    """
    _require_public_signup_enabled()

    import re
    from datetime import timedelta as _timedelta

    from app.models.tenant import TenantSettings
    from app.services.plans import get_plan

    plan = get_plan(body.plan)
    if plan is None or not plan.public_signup:
        raise HTTPException(status_code=403, detail="Plan is not available for signup")

    body.email = body.email.lower().strip()

    # Cross-tenant email-exists check with no tenant context: allow RLS bypass.
    await enable_rls_bypass(db)
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    slug = re.sub(r"[^a-z0-9]+", "-", body.firm_name.lower()).strip("-") or "firm"
    domain = f"{slug}-{uuid.uuid4().hex[:8]}"

    tenant = Tenant(
        id=uuid.uuid4(),
        name=body.firm_name,
        domain=domain,
        company_name=body.firm_name,
        staff_size=body.staff_size,
        address=body.address,
        phone=body.phone,
        billing_tier=plan.billing_tier,
        is_active=True,
    )
    db.add(tenant)
    await db.flush()

    trial_ends_at = (datetime.now(timezone.utc) + _timedelta(days=14)).isoformat()
    db.add(
        TenantSettings(
            tenant_id=tenant.id,
            custom_config={"plan": plan.id, "trial_ends_at": trial_ends_at},
        )
    )

    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=body.email,
        full_name=body.full_name or "",
        password_hash=_hash_password(body.password),
        role="admin",
        is_active=True,
        license_active=True,
    )
    db.add(user)
    await db.commit()
    await enable_rls_bypass(db)
    await db.refresh(user)
    await db.refresh(tenant)
    await ensure_stripe_customer(tenant, db)
    await db.commit()

    # New firm: seed system roles + assign the founding admin the Administrator
    # system role so the minted JWT carries manage_roles/admin_settings caps.
    from app.services.rbac_service import provision_tenant_rbac

    await provision_tenant_rbac(db, tenant.id, user.id)
    await db.commit()

    jwt_token = await _issue_access_token(db, user, tenant)
    refresh_token = await _create_refresh_token(request, user)
    _set_auth_cookies(response, jwt_token, refresh_token)
    return TokenResponse(
        user_id=str(user.id),
        tenant_id=str(tenant.id),
        role=user.role,
        email=user.email,
        full_name=user.full_name,
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    body.email = body.email.lower().strip()

    # Cross-tenant login-by-email lookup with no tenant context: allow RLS bypass.
    await enable_rls_bypass(db)
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
    jwt_token = await _issue_access_token(db, user, tenant)
    refresh_token = await _create_refresh_token(request, user)

    # Set hardened httpOnly access + refresh cookies.
    _set_auth_cookies(response, jwt_token, refresh_token)

    return TokenResponse(
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

    # Cross-tenant lookup-by-email with no tenant context: allow RLS bypass.
    await enable_rls_bypass(db)
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
        reset_url = (
            f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?"
            f"token={urllib.parse.quote(token)}"
        )
        safe_reset_url = escape(reset_url, quote=True)
        html_body = f"""
        <div style="font-family: Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.5;">
          <h2 style="margin: 0 0 16px;">Reset your LawHand password</h2>
          <p>Use the secure link below to choose a new password. This link expires in 30 minutes.</p>
          <p style="margin: 24px 0;">
            <a href="{safe_reset_url}"
               style="background:#0f2d5e;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:6px;font-weight:bold;display:inline-block;">
              Reset password
            </a>
          </p>
          <p style="font-size:12px;color:#6b7280;">If the button does not work, copy and paste this URL into your browser:<br>{safe_reset_url}</p>
          <p style="font-size:12px;color:#6b7280;">If you did not request this, you can ignore this message.</p>
        </div>
        """
        text_body = (
            "Reset your LawHand password\n\n"
            "Use this link within 30 minutes:\n"
            f"{reset_url}\n\n"
            "If you did not request this, you can ignore this message."
        )
        sent = await email_service.send_email(
            [user.email],
            "Reset your LawHand password",
            html_body,
            text_body,
        )
        if not sent:
            logger.error("Password reset email failed for user_id=%s", user.id)
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

    # Cross-tenant lookup-by-email with no tenant context: allow RLS bypass.
    await enable_rls_bypass(db)
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

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        # Revocation is only reliable with Redis (shared across all workers). The
        # per-worker in-memory blacklist below is a dev-only best-effort and must
        # NOT be relied on as a security control in multi-worker production.
        logger.warning(
            "Redis unavailable on logout: access-token revocation is per-worker "
            "(dev-only) and refresh-token revocation is skipped."
        )

    if token:
        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )
            jti = payload.get("jti")
            exp = payload.get("exp", 0)
            if jti and exp:
                ttl = max(0, exp - int(_time.time()))
                if redis:
                    await redis.setex(f"jti:{jti}", ttl, "1")
                else:
                    request.app.state.jti_blacklist[jti] = _time.time() + ttl
        except Exception:
            pass

    # Revoke the refresh-token rotation chain so it can't be replayed.
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token and redis:
        try:
            raw = await redis.get(_refresh_key(refresh_token))
            family = None
            if raw:
                data = _json.loads(
                    raw.decode("utf-8") if isinstance(raw, bytes) else raw
                )
                family = data.get("family")
            else:
                used_family = await redis.get(_refresh_used_key(refresh_token))
                if used_family:
                    family = _redis_text(used_family)
            if family:
                await _revoke_refresh_family(request, family)
            else:
                await redis.delete(
                    _refresh_key(refresh_token), _refresh_used_key(refresh_token)
                )
        except Exception:
            pass

    # Clear both auth cookies.
    response.delete_cookie("access_token", httponly=True, samesite="Lax", path="/")
    response.delete_cookie("refresh_token", httponly=True, samesite="Lax", path="/")

    return {"message": "Logged out successfully"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    """Rotate a refresh token: issue a fresh access + refresh token, single-use.

    Reads the refresh token from the ``refresh_token`` cookie (falling back to a
    JSON body ``{"refresh_token": ...}``). On success the presented token is
    consumed (deleted) and a new token in the SAME rotation family is issued.

    Reuse detection retains an expiring consumed-token tombstone for the
    remainder of that token's original lifetime. Replaying it identifies and
    atomically revokes the live family. Once the original credential would have
    expired, its tombstone also expires and later submissions are simply invalid.
    Redis is required; without it refresh is disabled.
    """
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(
            status_code=401, detail="Refresh unavailable (no session store)"
        )

    token = request.cookies.get("refresh_token")
    if not token:
        try:
            body = await request.json()
            token = (body or {}).get("refresh_token")
        except Exception:
            token = None
    if not token:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    consume_status, raw = await _consume_refresh_token(request, token)
    if consume_status == "replay":
        await _revoke_refresh_family(request, raw)
        raise HTTPException(status_code=401, detail="Refresh token reuse detected")
    if consume_status != "consumed":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    data = _json.loads(raw)
    family = data.get("family")
    user_id = data.get("user_id")

    # Auth-path cross-tenant lookup by id (no tenant context yet).
    await enable_rls_bypass(db)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        if family:
            await _revoke_refresh_family(request, family)
        raise HTTPException(status_code=401, detail="User not found or inactive")

    tenant = user.tenant
    try:
        require_active_tenant(tenant)
    except HTTPException:
        if family:
            await _revoke_refresh_family(request, family)
        raise
    access_token = await _issue_access_token(db, user, tenant)
    new_refresh = await _create_refresh_token(request, user, family=family)
    _set_auth_cookies(response, access_token, new_refresh)

    return TokenResponse(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        role=user.role,
        email=user.email,
        full_name=user.full_name,
    )


@router.get("/me", response_model=UserInfo)
async def get_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    enabled_modules, default_route = await resolve_enabled_modules(
        db, user.tenant_id, user=user
    )
    plan_id, upsell_target = await resolve_plan_meta(db, user.tenant_id)
    demo_session = await _active_demo_session(db, user.tenant_id)
    return UserInfo(
        id=str(user.id),
        tenant_id=str(user.tenant_id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        license_active=user.license_active,
        premium_ai_enabled=user.premium_ai_enabled,
        created_at=user.created_at,
        billing_tier=user.tenant.billing_tier if user.tenant else "payg",
        enabled_modules=enabled_modules,
        default_route=default_route,
        plan=plan_id,
        upsell_target=upsell_target,
        professional_role=user.professional_role,
        job_title=user.job_title,
        office_location=user.office_location,
        primary_jurisdictions=user.primary_jurisdictions or [],
        privacy_mode=user.privacy_mode,
        demo=(
            {
                "session_id": str(demo_session.id),
                "expires_at": demo_session.expires_at,
                "quota": demo_session.quota,
                "reserved": demo_session.reserved,
                "used": demo_session.used,
            }
            if demo_session
            else None
        ),
    )


@router.patch("/me", response_model=UserInfo)
async def update_me(
    body: UserProfileUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update only the caller's verified professional profile fields."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    enabled_modules, default_route = await resolve_enabled_modules(
        db, user.tenant_id, user=user
    )
    plan_id, upsell_target = await resolve_plan_meta(db, user.tenant_id)
    return UserInfo(
        id=str(user.id),
        tenant_id=str(user.tenant_id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        license_active=user.license_active,
        premium_ai_enabled=user.premium_ai_enabled,
        created_at=user.created_at,
        billing_tier=user.tenant.billing_tier if user.tenant else "payg",
        enabled_modules=enabled_modules,
        default_route=default_route,
        plan=plan_id,
        upsell_target=upsell_target,
        professional_role=user.professional_role,
        job_title=user.job_title,
        office_location=user.office_location,
        primary_jurisdictions=user.primary_jurisdictions or [],
        privacy_mode=user.privacy_mode,
    )


@router.get("/calendar-providers")
async def get_calendar_providers(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    from app.services.token_vault import get_fresh_user_token

    tenant_id = str(user.tenant_id)
    user_id = str(user.id)
    await set_tenant_context(db, tenant_id)

    user_rows_result = await db.execute(
        select(UserOAuthToken).where(
            UserOAuthToken.tenant_id == user.tenant_id,
            UserOAuthToken.user_id == user.id,
            UserOAuthToken.provider.in_(["microsoft", "google"]),
        )
    )
    user_rows = {row.provider: row for row in user_rows_result.scalars().all()}

    providers = []
    provider_status = {}
    for provider in ("microsoft", "google"):
        row = user_rows.get(provider)
        required_scopes = CALENDAR_REQUIRED_SCOPES[provider]
        granted_scopes = set((row.scopes or "").split()) if row else set()
        missing_scopes = sorted(required_scopes - granted_scopes) if row else []

        token = None
        reason = "not_connected"
        if row and missing_scopes:
            reason = "missing_scopes"
        elif row:
            token = await get_fresh_user_token(db, tenant_id, user_id, provider)
            reason = None if token else "refresh_failed"

        connected = bool(token)
        if connected:
            providers.append(provider)

        provider_status[provider] = {
            "connected": connected,
            "needs_reconnect": bool(row) and not connected,
            "reason": reason,
            "missing_scopes": missing_scopes,
            "expires_at": row.token_expires_at.isoformat()
            if row and row.token_expires_at
            else None,
            "has_refresh_token": bool(row and row.encrypted_refresh_token),
        }

    tenant_rows = await db.execute(
        select(TenantCredential.provider).where(
            TenantCredential.tenant_id == user.tenant_id,
            TenantCredential.is_active.is_(True),
            TenantCredential.provider.in_(["microsoft", "google"]),
        )
    )
    tenant_providers = list(dict.fromkeys(tenant_rows.scalars().all()))
    login_provider = user.oauth_provider if user.oauth_provider in providers else None
    if not login_provider and user.oauth_provider in ("microsoft", "google"):
        login_provider = user.oauth_provider
    connect_provider = login_provider or (
        tenant_providers[0] if tenant_providers else None
    )

    return {
        "providers": providers,
        "tenant_providers": tenant_providers,
        "login_provider": login_provider,
        "connect_provider": connect_provider,
        "provider_status": provider_status,
    }
