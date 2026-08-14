import base64
import asyncio
import hashlib
import json as _json
import logging
import re
import secrets
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker, get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.tenant_credential import TenantCredential
from app.models.tenant import Tenant
from app.models.durable_job import DurableJob
from app.models.user import User
from app.models.user_oauth_token import UserOAuthToken
from app.schemas.integrations import IntegrationStatus, IntegrationsListResponse
from app.services.teams import TEAMS_CONNECT_SCOPES
from app.services.teams_gate import missing_teams_scopes
from app.services.token_vault import decrypt_token, encrypt_token, revoke_provider_token
from app.services.integration_observability import apply_scope_audit, missing_scopes
from app.services.durable_jobs import enqueue_job
from app.services.connected_mail import (
    GOOGLE_MAIL_SEND_SCOPE,
    MICROSOFT_MAIL_SEND_SCOPE,
)
from app.services.tenant_oauth_apps import (
    get_tenant_oauth_app,
    get_zoom_phone_webhook_secret,
    get_zoom_phone_oauth_client,
    mask_client_id,
    upsert_zoom_phone_oauth_app,
)
from app.services.zoom_phone import (
    ZOOM_PHONE_HISTORY_COMPLETED_EVENTS,
    ZOOM_PHONE_PROVIDER,
    ZoomPhoneIntegrationError,
    probe_zoom_phone_connection,
    verify_zoom_webhook_signature,
    zoom_phone_webhook_jobs,
    zoom_webhook_validation_response,
)
from app.utils.oauth_security import (
    generate_pkce_pair,
    is_oauth_client_configured,
)

settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/integrations", tags=["integrations"])

_STATE_TTL = 600
ZOOM_WEBHOOK_MAX_BODY_BYTES = 256 * 1024
_fallback_states: dict[str, float] = {}
_fallback_state_data: dict[str, dict] = {}


class ZoomPhoneAppCredentialRequest(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    webhook_secret_token: str = ""
    zoom_account_id: str = ""


_ZOOM_ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,255}$")


def _verified_zoom_account_binding(account_id: str | None) -> str | None:
    """Return a provider-issued webhook account id, ignoring legacy numbers.

    An earlier UI mislabeled Zoom's human-facing numeric Account Number as the
    API ``account_id``.  Numeric-only values already stored by that workflow are
    deliberately treated as unbound so existing grants keep syncing and the
    next signed, provider-fetched event can replace them safely.
    """

    normalized = str(account_id or "").strip()
    if (
        not normalized
        or normalized.isdecimal()
        or not _ZOOM_ACCOUNT_ID_PATTERN.fullmatch(normalized)
    ):
        return None
    return normalized


async def _reset_failed_zoom_account_binding_jobs(
    db: AsyncSession,
    *,
    tenant_id: str,
    account_id: str | None,
) -> int:
    """Retry narrowly tagged signed exact-call proofs after reauthorization.

    A wrong-account grant fails its proof job permanently so it cannot hammer
    Zoom. Once the administrator installs a new grant, the original signed event
    is still valid evidence. Zoom often omits ``account_id`` from token responses,
    so an absent filter retries all valid binding-proof jobs for this tenant; an
    available provider binding narrows retries to that account.
    """

    rows = list(
        (
            await db.scalars(
                select(DurableJob).where(
                    DurableJob.tenant_id == uuid.UUID(str(tenant_id)),
                    DurableJob.kind == "zoom_phone_call_import",
                    DurableJob.status == "failed",
                )
            )
        ).all()
    )
    reset = 0
    expected_account_id = _verified_zoom_account_binding(account_id)
    for row in rows:
        payload = dict(row.payload or {})
        binding = payload.get("account_binding")
        binding_account_id = (
            _verified_zoom_account_binding(binding.get("account_id"))
            if isinstance(binding, dict)
            and binding.get("proof") == "signed_event_exact_call_fetch"
            else None
        )

        # Jobs created by the short-lived blocking workflow still represent a
        # signed v3 exact-call attempt. Convert them in place so a reauthorization
        # can recover them through the current binding lifecycle.
        if not binding_account_id:
            legacy = payload.get("account_verification")
            if (
                isinstance(legacy, dict)
                and legacy.get("proof") == "signed_v3_call_element"
                and payload.get("event_name")
                in {
                    "phone.callee_call_element_completed",
                    "phone.caller_call_element_completed",
                }
                and payload.get("call_element_id")
                and payload.get("call_history_id")
            ):
                binding_account_id = _verified_zoom_account_binding(
                    legacy.get("account_id")
                )
                if binding_account_id:
                    payload.pop("account_verification", None)
                    payload["account_binding"] = {
                        "account_id": binding_account_id,
                        "proof": "signed_event_exact_call_fetch",
                    }

        if (
            not binding_account_id
            or payload.get("event_name") not in ZOOM_PHONE_HISTORY_COMPLETED_EVENTS
            or not payload.get("stable_call_id")
            or not (payload.get("call_element_id") or payload.get("call_history_id"))
            or (
                expected_account_id
                and not secrets.compare_digest(binding_account_id, expected_account_id)
            )
        ):
            continue
        row.payload = payload
        row.status = "pending"
        row.progress = 0
        row.attempts = 0
        row.available_at = datetime.now(timezone.utc)
        row.leased_at = None
        row.lease_owner = None
        row.last_error = None
        row.result = None
        row.completed_at = None
        reset += 1
    return reset


def _gc_fallback_states() -> None:
    """Evict expired in-process state entries (only relevant when Redis is absent)."""
    now = _time.time()
    for key, ts in list(_fallback_states.items()):
        if now - ts > _STATE_TTL:
            del _fallback_states[key]
            _fallback_state_data.pop(key, None)


async def _save_state(request: Request, state: str, data: dict | None = None) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.setex(f"integration:state:{state}", _STATE_TTL, "1")
        if data:
            await redis.setex(
                f"integration:statedata:{state}", _STATE_TTL, _json.dumps(data)
            )
    else:
        _gc_fallback_states()
        _fallback_states[state] = _time.time()
        if data:
            _fallback_state_data[state] = data


async def _consume_state(request: Request, state: str) -> tuple[bool, dict | None]:
    redis = getattr(request.app.state, "redis", None)
    data = None
    if redis:
        deleted = await redis.delete(f"integration:state:{state}")
        if deleted:
            raw = await redis.get(f"integration:statedata:{state}")
            if raw:
                data = _json.loads(raw)
            await redis.delete(f"integration:statedata:{state}")
        return bool(deleted), data
    ts = _fallback_states.pop(state, None)
    if ts is None:
        return False, None
    data = _fallback_state_data.pop(state, None)
    if _time.time() - ts > _STATE_TTL:
        return False, None
    return True, data


# ── Admin Connect flows (tenant-wide admin consent) ────────────────────────

MICROSOFT_ADMIN_SCOPES = (
    f"offline_access User.Read.All Mail.Read {MICROSOFT_MAIL_SEND_SCOPE} "
    "Files.ReadWrite.All Sites.Read.All Calendars.ReadWrite"
)
GOOGLE_ADMIN_SCOPES = (
    "openid email profile "
    "https://www.googleapis.com/auth/admin.directory.user.readonly "
    "https://www.googleapis.com/auth/gmail.readonly "
    f"{GOOGLE_MAIL_SEND_SCOPE} "
    "https://www.googleapis.com/auth/calendar "
    "https://www.googleapis.com/auth/drive"
)

# Per-user (intent=user) scopes. These MUST be used for both the authorize URL
# and the token exchange so the two can never drift apart.
MICROSOFT_USER_SCOPES = (
    f"offline_access User.Read Mail.Read {MICROSOFT_MAIL_SEND_SCOPE} "
    "Files.ReadWrite.All Calendars.ReadWrite"
)
GOOGLE_USER_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly "
    f"{GOOGLE_MAIL_SEND_SCOPE} "
    "https://www.googleapis.com/auth/calendar "
    "https://www.googleapis.com/auth/drive"
)
ZOOM_SCOPES = "meeting:write meeting:read user:read"

SCOPE_ALIASES_GOOGLE = {
    "email": {"email", "https://www.googleapis.com/auth/userinfo.email"},
    "profile": {"profile", "https://www.googleapis.com/auth/userinfo.profile"},
}


def _expires_at(expires_in: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


def _scope_is_granted(required_scope: str, granted: set[str], provider: str) -> bool:
    if required_scope in granted:
        return True
    if provider == "google":
        return bool(SCOPE_ALIASES_GOOGLE.get(required_scope, set()) & granted)
    return False


def _require_state_user(meta: dict | None, intent: str) -> tuple[str, str]:
    if not meta or meta.get("intent") != intent:
        raise HTTPException(status_code=400, detail="Invalid integration state")
    user_id = meta.get("user_id")
    tenant_id = meta.get("tenant_id")
    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=400, detail="Integration state is missing user context"
        )
    if intent == "admin" and meta.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user_id, tenant_id


def _admin_scopes(teams: bool) -> str:
    """Admin consent scope string, optionally widened with Teams scopes.

    Teams scopes are only appended on explicit opt-in (``&teams=1``) so existing
    cloud-only tenants are never forced into broader consent.
    """
    if teams:
        return MICROSOFT_ADMIN_SCOPES + " " + TEAMS_CONNECT_SCOPES
    return MICROSOFT_ADMIN_SCOPES


def _zoom_redirect_uri() -> str:
    return (
        settings.ZOOM_REDIRECT_URI
        or f"{settings.BACKEND_URL}/api/integrations/zoom/callback"
    )


def _zoom_phone_redirect_uri() -> str:
    return (
        settings.ZOOM_PHONE_REDIRECT_URI
        or f"{settings.BACKEND_URL}/api/integrations/zoom-phone/callback"
    )


def _zoom_phone_webhook_uri(tenant_id: str | None = None) -> str:
    suffix = f"/{tenant_id}" if tenant_id else ""
    return f"{settings.BACKEND_URL}/api/integrations/zoom-phone/webhook{suffix}"


def _missing_scopes(provider: str, granted: str | None, required: str) -> list[str]:
    if not required:
        return []
    if not granted:
        return required.split()
    granted_set = set(granted.split())
    return sorted(
        scope
        for scope in required.split()
        if not _scope_is_granted(scope, granted_set, provider)
    )


def _app_credentials_payload(app, *, source: str | None, platform_ready: bool) -> dict:
    client_id_hint = None
    if app:
        try:
            client_id_hint = mask_client_id(decrypt_token(app.encrypted_client_id))
        except Exception:
            logger.warning("Zoom Phone tenant app client ID decrypt failed")
    account_binding = _verified_zoom_account_binding(
        getattr(app, "zoom_account_id", None) if app else None
    )
    return {
        "configured": bool(app),
        "source": source,
        "client_id_hint": client_id_hint,
        "zoom_account_id": account_binding,
        "zoom_account_id_configured": bool(account_binding),
        "platform_app_configured": platform_ready,
        "redirect_uri": _zoom_phone_redirect_uri(),
        "webhook_url": _zoom_phone_webhook_uri(str(app.tenant_id) if app else None),
        "webhook_secret_configured": bool(
            getattr(app, "encrypted_webhook_secret_token", None)
        ),
        "required_scopes": settings.ZOOM_PHONE_SCOPES.split(),
    }


@router.get("/microsoft/connect")
async def microsoft_connect(
    request: Request,
    intent: str = Query("admin", description="admin=tenant-wide, user=per-user"),
    teams: int = Query(0, description="1=include Microsoft Teams scopes"),
    db: AsyncSession = Depends(get_db),
):
    if not is_oauth_client_configured(
        settings.MICROSOFT_CLIENT_ID, settings.MICROSOFT_CLIENT_SECRET
    ):
        raise HTTPException(status_code=501, detail="Microsoft OAuth not configured")

    user = await get_current_user(request, db)
    if intent == "admin" and user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    teams_flag = bool(teams)
    if intent == "admin" and not teams_flag:
        await set_tenant_context(db, str(user.tenant_id))
        existing = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == user.tenant_id,
                TenantCredential.provider == "microsoft",
                TenantCredential.is_active,
            )
        )
        cred = existing.scalar_one_or_none()
        if cred and not missing_teams_scopes(cred.scopes):
            teams_flag = True

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()
    await _save_state(
        request,
        state,
        {
            "intent": intent,
            "provider": "microsoft",
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
            "teams": teams_flag,
            "pkce_verifier": code_verifier,
        },
    )

    ms_tenant = settings.MICROSOFT_TENANT_ID
    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/microsoft/callback"
    scopes = _admin_scopes(teams_flag) if intent == "admin" else MICROSOFT_USER_SCOPES

    authorize_url = (
        f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/authorize"
        f"?client_id={settings.MICROSOFT_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes.replace(' ', '+')}"
        f"&state={state}"
        f"&response_mode=query"
        f"&prompt=select_account"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )
    return RedirectResponse(url=authorize_url)


@router.get("/microsoft/callback")
async def microsoft_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, meta = await _consume_state(request, state)
    if not valid:
        return _error_redirect("microsoft", "invalid_state")
    if meta and meta.get("provider") not in (None, "microsoft"):
        return _error_redirect("microsoft", "invalid_state")

    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/microsoft/callback"
    ms_tenant = settings.MICROSOFT_TENANT_ID
    token_url = f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/token"

    intent = meta.get("intent", "user") if meta else "user"
    teams_flag = bool(meta.get("teams")) if meta else False
    code_verifier = meta.get("pkce_verifier") if meta else None

    expected_scopes = (
        _admin_scopes(teams_flag) if intent == "admin" else MICROSOFT_USER_SCOPES
    )
    token_payload = {
        "client_id": settings.MICROSOFT_CLIENT_ID,
        "client_secret": settings.MICROSOFT_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "scope": expected_scopes,
    }
    if code_verifier:
        token_payload["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=30.0) as client:
        token_resp = await client.post(
            token_url,
            data=token_payload,
        )
        if token_resp.status_code != 200:
            return _error_redirect("microsoft", "token_exchange_failed")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)
        scope_str = token_data.get("scope", "")

        # Microsoft sometimes omits "offline_access" from the returned scope
        # string even when it was requested and granted. The presence of a
        # refresh_token is the authoritative signal that offline_access was
        # granted — ensure it appears in the stored scopes.
        if refresh_token and "offline_access" not in scope_str:
            scope_str = ("offline_access " + scope_str).strip()

        if not access_token:
            return _error_redirect("microsoft", "no_access_token")

        if intent == "admin":
            _user_id, tenant_id = _require_state_user(meta, "admin")
            admin_user_id = _user_id
            await set_tenant_context(db, tenant_id)

            # Resolve service account email from id_token (same approach as auth callback)
            service_email = None
            id_token_raw = token_data.get("id_token")
            if id_token_raw:
                try:
                    payload_b64 = id_token_raw.split(".")[1]
                    payload_b64 += "=" * (4 - len(payload_b64) % 4)
                    claims = _json.loads(base64.urlsafe_b64decode(payload_b64))
                    service_email = (
                        claims.get("email")
                        or claims.get("preferred_username")
                        or claims.get("upn")
                    )
                except Exception:
                    pass

            result = await db.execute(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == tenant_id,
                    TenantCredential.provider == "microsoft",
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.encrypted_access_token = encrypt_token(access_token)
                existing.encrypted_refresh_token = (
                    encrypt_token(refresh_token) if refresh_token else None
                )
                existing.token_expires_at = _expires_at(expires_in)
                existing.scopes = scope_str
                existing.is_active = True
                existing.granted_by_user_id = uuid.UUID(admin_user_id)
                if service_email:
                    existing.service_account_email = service_email
                cred_row = existing
            else:
                cred_row = TenantCredential(
                    tenant_id=uuid.UUID(tenant_id),
                    provider="microsoft",
                    encrypted_access_token=encrypt_token(access_token),
                    encrypted_refresh_token=(
                        encrypt_token(refresh_token) if refresh_token else None
                    ),
                    token_expires_at=_expires_at(expires_in),
                    scopes=scope_str,
                    granted_by_user_id=uuid.UUID(admin_user_id),
                    service_account_email=service_email,
                )
                db.add(cred_row)
            apply_scope_audit(cred_row, "microsoft", expected_scopes, _scope_is_granted)
        else:
            user_id, tenant_id = _require_state_user(meta, "user")
            await set_tenant_context(db, tenant_id)
            existing = await db.execute(
                select(UserOAuthToken).where(
                    UserOAuthToken.user_id == user_id,
                    UserOAuthToken.tenant_id == tenant_id,
                    UserOAuthToken.provider == "microsoft",
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                row.encrypted_access_token = encrypt_token(access_token)
                row.encrypted_refresh_token = (
                    encrypt_token(refresh_token) if refresh_token else None
                )
                row.token_expires_at = _expires_at(expires_in)
                row.scopes = scope_str
            else:
                row = UserOAuthToken(
                    user_id=uuid.UUID(user_id),
                    tenant_id=uuid.UUID(tenant_id),
                    provider="microsoft",
                    encrypted_access_token=encrypt_token(access_token),
                    encrypted_refresh_token=(
                        encrypt_token(refresh_token) if refresh_token else None
                    ),
                    token_expires_at=_expires_at(expires_in),
                    scopes=scope_str,
                )
                db.add(row)
            apply_scope_audit(
                row, "microsoft", MICROSOFT_USER_SCOPES, _scope_is_granted
            )

        await db.commit()

        if intent == "admin":
            await _onboarding_post_connect(db, tenant_id, "microsoft")
            await _ensure_cloud_root(db, tenant_id)
            _schedule_user_sync_post_connect(tenant_id, "microsoft")

    return await _post_connect_redirect(db, tenant_id, "microsoft")


@router.get("/google/connect")
async def google_connect(
    request: Request,
    intent: str = Query("admin", description="admin=tenant-wide, user=per-user"),
    db: AsyncSession = Depends(get_db),
):
    if not is_oauth_client_configured(
        settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET
    ):
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    user = await get_current_user(request, db)
    if intent == "admin" and user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()
    await _save_state(
        request,
        state,
        {
            "intent": intent,
            "provider": "google",
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
            "pkce_verifier": code_verifier,
        },
    )

    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/google/callback"
    scopes = GOOGLE_ADMIN_SCOPES if intent == "admin" else GOOGLE_USER_SCOPES

    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes.replace(' ', '+')}"
        "&access_type=offline"
        "&prompt=consent"
        f"&state={state}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )
    return RedirectResponse(url=authorize_url)


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, meta = await _consume_state(request, state)
    if not valid:
        return _error_redirect("google", "invalid_state")
    if meta and meta.get("provider") not in (None, "google"):
        return _error_redirect("google", "invalid_state")

    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/google/callback"
    intent = meta.get("intent", "user") if meta else "user"
    code_verifier = meta.get("pkce_verifier") if meta else None

    token_payload = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        token_payload["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=30.0) as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data=token_payload,
        )
        if token_resp.status_code != 200:
            return _error_redirect("google", "token_exchange_failed")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)
        scope_str = token_data.get("scope", "")

        if not access_token:
            return _error_redirect("google", "no_access_token")

        if intent == "admin":
            _user_id, tenant_id = _require_state_user(meta, "admin")
            admin_user_id = _user_id
            await set_tenant_context(db, tenant_id)

            # Resolve service account email from Google id_token
            service_email = None
            id_token = token_data.get("id_token")
            if id_token:
                try:
                    payload = id_token.split(".")[1]
                    # Add padding
                    payload += "=" * (4 - len(payload) % 4)
                    decoded = _json.loads(base64.urlsafe_b64decode(payload))
                    service_email = decoded.get("email")
                except Exception:
                    pass

            existing = await db.execute(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == tenant_id,
                    TenantCredential.provider == "google",
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                row.encrypted_access_token = encrypt_token(access_token)
                row.encrypted_refresh_token = (
                    encrypt_token(refresh_token) if refresh_token else None
                )
                row.token_expires_at = _expires_at(expires_in)
                row.scopes = scope_str
                row.is_active = True
                row.granted_by_user_id = uuid.UUID(admin_user_id)
                if service_email:
                    row.service_account_email = service_email
            else:
                row = TenantCredential(
                    tenant_id=uuid.UUID(tenant_id),
                    provider="google",
                    encrypted_access_token=encrypt_token(access_token),
                    encrypted_refresh_token=(
                        encrypt_token(refresh_token) if refresh_token else None
                    ),
                    token_expires_at=_expires_at(expires_in),
                    scopes=scope_str,
                    granted_by_user_id=uuid.UUID(admin_user_id),
                    service_account_email=service_email,
                )
                db.add(row)
            apply_scope_audit(row, "google", GOOGLE_ADMIN_SCOPES, _scope_is_granted)
        else:
            user_id, tenant_id = _require_state_user(meta, "user")
            await set_tenant_context(db, tenant_id)
            existing = await db.execute(
                select(UserOAuthToken).where(
                    UserOAuthToken.user_id == user_id,
                    UserOAuthToken.tenant_id == tenant_id,
                    UserOAuthToken.provider == "google",
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                row.encrypted_access_token = encrypt_token(access_token)
                row.encrypted_refresh_token = (
                    encrypt_token(refresh_token) if refresh_token else None
                )
                row.token_expires_at = _expires_at(expires_in)
                row.scopes = scope_str
            else:
                row = UserOAuthToken(
                    user_id=uuid.UUID(user_id),
                    tenant_id=uuid.UUID(tenant_id),
                    provider="google",
                    encrypted_access_token=encrypt_token(access_token),
                    encrypted_refresh_token=(
                        encrypt_token(refresh_token) if refresh_token else None
                    ),
                    token_expires_at=_expires_at(expires_in),
                    scopes=scope_str,
                )
                db.add(row)
            apply_scope_audit(row, "google", GOOGLE_USER_SCOPES, _scope_is_granted)

        await db.commit()

        if intent == "admin":
            await _onboarding_post_connect(db, tenant_id, "google")
            await _ensure_cloud_root(db, tenant_id)
            _schedule_user_sync_post_connect(tenant_id, "google")

    return await _post_connect_redirect(db, tenant_id, "google")


@router.get("/zoom/connect")
async def zoom_connect(
    request: Request,
    intent: str = Query(
        "user", description="user=per-user, admin=tenant-wide shared Zoom"
    ),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if intent == "admin" and user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    if not settings.ZOOM_CLIENT_ID or not settings.ZOOM_CLIENT_SECRET:
        raise HTTPException(status_code=501, detail="Zoom OAuth not configured")

    state = secrets.token_urlsafe(32)
    await _save_state(
        request,
        state,
        {
            "intent": intent,
            "provider": "zoom",
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
        },
    )

    redirect_uri = _zoom_redirect_uri()
    authorize_url = "https://zoom.us/oauth/authorize?" + urlencode(
        {
            "response_type": "code",
            "client_id": settings.ZOOM_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return RedirectResponse(authorize_url)


@router.get("/zoom/callback")
async def zoom_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, meta = await _consume_state(request, state)
    if not valid:
        return _error_redirect("zoom", "invalid_state")
    intent = meta.get("intent", "user") if meta else "user"
    user_id, tenant_id = _require_state_user(meta, intent)
    await set_tenant_context(db, tenant_id)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://zoom.us/oauth/token",
            auth=(settings.ZOOM_CLIENT_ID, settings.ZOOM_CLIENT_SECRET),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _zoom_redirect_uri(),
            },
        )
    if token_resp.status_code != 200:
        logger.warning(
            "Zoom token exchange failed: %s %s",
            token_resp.status_code,
            token_resp.text[:300],
        )
        return _error_redirect("zoom", "token_exchange_failed")

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    scope_str = token_data.get("scope") or ZOOM_SCOPES
    if not access_token:
        return _error_redirect("zoom", "no_access_token")

    if intent == "admin":
        result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == "zoom",
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.encrypted_access_token = encrypt_token(access_token)
            row.encrypted_refresh_token = (
                encrypt_token(refresh_token) if refresh_token else None
            )
            row.token_expires_at = _expires_at(expires_in)
            row.scopes = scope_str
            row.is_active = True
            row.granted_by_user_id = uuid.UUID(user_id)
        else:
            db.add(
                TenantCredential(
                    tenant_id=uuid.UUID(tenant_id),
                    provider="zoom",
                    encrypted_access_token=encrypt_token(access_token),
                    encrypted_refresh_token=encrypt_token(refresh_token)
                    if refresh_token
                    else None,
                    token_expires_at=_expires_at(expires_in),
                    scopes=scope_str,
                    granted_by_user_id=uuid.UUID(user_id),
                )
            )
    else:
        result = await db.execute(
            select(UserOAuthToken).where(
                UserOAuthToken.user_id == user_id,
                UserOAuthToken.tenant_id == tenant_id,
                UserOAuthToken.provider == "zoom",
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.encrypted_access_token = encrypt_token(access_token)
            row.encrypted_refresh_token = (
                encrypt_token(refresh_token) if refresh_token else None
            )
            row.token_expires_at = _expires_at(expires_in)
            row.scopes = scope_str
        else:
            db.add(
                UserOAuthToken(
                    user_id=uuid.UUID(user_id),
                    tenant_id=uuid.UUID(tenant_id),
                    provider="zoom",
                    encrypted_access_token=encrypt_token(access_token),
                    encrypted_refresh_token=encrypt_token(refresh_token)
                    if refresh_token
                    else None,
                    token_expires_at=_expires_at(expires_in),
                    scopes=scope_str,
                )
            )
    await db.commit()
    return await _post_connect_redirect(db, tenant_id, "zoom")


@router.get("/zoom-phone/connect")
async def zoom_phone_connect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    tenant_id = str(user.tenant_id)
    await set_tenant_context(db, tenant_id)
    oauth_client = await get_zoom_phone_oauth_client(db, tenant_id=tenant_id)
    if not oauth_client:
        raise HTTPException(
            status_code=409,
            detail=(
                "Zoom Phone OAuth app credentials are not configured. Complete "
                "your firm's Zoom app setup first."
            ),
        )

    state = secrets.token_urlsafe(32)
    await _save_state(
        request,
        state,
        {
            "intent": "admin",
            "provider": ZOOM_PHONE_PROVIDER,
            "user_id": str(user.id),
            "tenant_id": tenant_id,
            "role": user.role,
            "oauth_app_source": oauth_client.source,
            "oauth_client_id_fingerprint": hashlib.sha256(
                oauth_client.client_id.encode("utf-8")
            ).hexdigest(),
        },
    )

    authorize_url = "https://zoom.us/oauth/authorize?" + urlencode(
        {
            "response_type": "code",
            "client_id": oauth_client.client_id,
            "redirect_uri": _zoom_phone_redirect_uri(),
            "state": state,
        }
    )
    return RedirectResponse(authorize_url)


@router.get("/zoom-phone/callback")
async def zoom_phone_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, meta = await _consume_state(request, state)
    if not valid:
        return _error_redirect(ZOOM_PHONE_PROVIDER, "invalid_state")
    user_id, tenant_id = _require_state_user(meta, "admin")
    if meta.get("provider") != ZOOM_PHONE_PROVIDER:
        return _error_redirect(ZOOM_PHONE_PROVIDER, "invalid_state")
    await set_tenant_context(db, tenant_id)
    tenant_active = await db.scalar(
        select(Tenant.is_active).where(Tenant.id == uuid.UUID(tenant_id))
    )
    current_admin = await db.scalar(
        select(User.id).where(
            User.id == uuid.UUID(user_id),
            User.tenant_id == uuid.UUID(tenant_id),
            User.is_active.is_(True),
            User.role == "admin",
        )
    )
    if not tenant_active or not current_admin:
        return _error_redirect(ZOOM_PHONE_PROVIDER, "authorization_revoked")
    oauth_client = await get_zoom_phone_oauth_client(db, tenant_id=tenant_id)
    if not oauth_client:
        return _error_redirect(ZOOM_PHONE_PROVIDER, "app_credentials_missing")
    expected_client_fingerprint = str(meta.get("oauth_client_id_fingerprint") or "")
    current_client_fingerprint = hashlib.sha256(
        oauth_client.client_id.encode("utf-8")
    ).hexdigest()
    if not expected_client_fingerprint or not secrets.compare_digest(
        expected_client_fingerprint, current_client_fingerprint
    ):
        return _error_redirect(ZOOM_PHONE_PROVIDER, "app_credentials_changed")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://zoom.us/oauth/token",
            auth=(oauth_client.client_id, oauth_client.client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _zoom_phone_redirect_uri(),
            },
        )
    if token_resp.status_code != 200:
        logger.warning(
            "Zoom Phone token exchange failed: %s %s",
            token_resp.status_code,
            token_resp.text[:300],
        )
        return _error_redirect(ZOOM_PHONE_PROVIDER, "token_exchange_failed")

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    scope_str = token_data.get("scope") or settings.ZOOM_PHONE_SCOPES
    if not access_token:
        return _error_redirect(ZOOM_PHONE_PROVIDER, "no_access_token")
    if not refresh_token:
        return _error_redirect(ZOOM_PHONE_PROVIDER, "refresh_token_missing")
    returned_account_id = _verified_zoom_account_binding(token_data.get("account_id"))
    existing_binding = _verified_zoom_account_binding(oauth_client.account_id)
    if (
        returned_account_id
        and existing_binding
        and not secrets.compare_digest(returned_account_id, existing_binding)
    ):
        logger.warning(
            "Zoom Phone token account does not match the tenant app mapping "
            "for tenant=%s",
            tenant_id,
        )
        return _error_redirect(ZOOM_PHONE_PROVIDER, "account_mapping_mismatch")
    # API connectivity and webhook account binding are intentionally separate.
    # A refreshable grant becomes usable immediately; when Zoom supplies its
    # opaque account id we also bind real-time webhook delivery automatically.
    if returned_account_id:
        app = await get_tenant_oauth_app(
            db, tenant_id=tenant_id, provider=ZOOM_PHONE_PROVIDER
        )
        if app:
            app.zoom_account_id = returned_account_id

    result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == ZOOM_PHONE_PROVIDER,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.encrypted_access_token = encrypt_token(access_token)
        row.encrypted_refresh_token = encrypt_token(refresh_token)
        row.token_expires_at = _expires_at(expires_in)
        row.scopes = scope_str
        row.is_active = True
        row.health = "healthy"
        row.last_refresh_error = None
        row.granted_by_user_id = uuid.UUID(user_id)
        row.service_account_email = returned_account_id or existing_binding
    else:
        db.add(
            TenantCredential(
                tenant_id=uuid.UUID(tenant_id),
                provider=ZOOM_PHONE_PROVIDER,
                encrypted_access_token=encrypt_token(access_token),
                encrypted_refresh_token=encrypt_token(refresh_token)
                if refresh_token
                else None,
                token_expires_at=_expires_at(expires_in),
                scopes=scope_str,
                granted_by_user_id=uuid.UUID(user_id),
                service_account_email=returned_account_id or existing_binding,
                health="healthy",
                last_refresh_error=None,
            )
        )
    await _reset_failed_zoom_account_binding_jobs(
        db,
        tenant_id=tenant_id,
        account_id=returned_account_id or existing_binding,
    )
    await db.commit()
    try:
        # Prove the exact account-scoped API used by intake before presenting
        # the callback as connected. This does not depend on a future webhook.
        await probe_zoom_phone_connection(db, tenant_id=tenant_id)
    except ZoomPhoneIntegrationError as exc:
        logger.warning("Zoom Phone post-OAuth API probe failed: %s", exc)
        return _error_redirect(ZOOM_PHONE_PROVIDER, "phone_api_probe_failed")
    return await _post_connect_redirect(db, tenant_id, ZOOM_PHONE_PROVIDER)


async def _resolve_zoom_phone_webhook_tenant(
    db: AsyncSession,
    event: dict,
    tenant_id: str | None,
) -> str | None:
    if tenant_id:
        tenant_uuid = uuid.UUID(str(tenant_id))
        active = await db.scalar(
            select(Tenant.id).where(
                Tenant.id == tenant_uuid,
                Tenant.is_active.is_(True),
            )
        )
        return str(active) if active else None
    account_id = (
        ((event.get("payload") or {}).get("account_id"))
        if isinstance(event.get("payload"), dict)
        else None
    )
    if not account_id:
        return None
    # The credential table is forced-RLS. Enumerate active tenants from the
    # non-tenant-scoped tenant registry, then perform each account lookup under
    # that tenant's normal RLS context. This preserves support for the legacy
    # shared webhook URL without introducing a cross-tenant bypass.
    active_tenant_ids = list(
        (
            await db.execute(
                select(Tenant.id).where(Tenant.is_active.is_(True)).order_by(Tenant.id)
            )
        ).scalars()
    )
    for active_tenant_id in active_tenant_ids:
        await set_tenant_context(db, str(active_tenant_id))
        row = (
            await db.execute(
                select(TenantCredential).where(
                    TenantCredential.tenant_id == active_tenant_id,
                    TenantCredential.provider == ZOOM_PHONE_PROVIDER,
                    TenantCredential.service_account_email == account_id,
                    TenantCredential.is_active,
                )
            )
        ).scalar_one_or_none()
        if row:
            return str(row.tenant_id)
    return None


async def _handle_zoom_phone_webhook(
    request: Request,
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
) -> dict:
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            parsed_length = int(declared_length)
            if parsed_length < 0:
                raise ValueError
            if parsed_length > ZOOM_WEBHOOK_MAX_BODY_BYTES:
                raise HTTPException(
                    status_code=413, detail="Zoom webhook payload is too large."
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid Content-Length header."
            ) from exc
    chunks: list[bytes] = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > ZOOM_WEBHOOK_MAX_BODY_BYTES:
            raise HTTPException(
                status_code=413, detail="Zoom webhook payload is too large."
            )
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        event = _json.loads(body.decode("utf-8") if body else "{}")
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid Zoom webhook payload"
        ) from exc
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Invalid Zoom webhook payload")

    resolved_tenant_id = await _resolve_zoom_phone_webhook_tenant(db, event, tenant_id)
    if tenant_id and not resolved_tenant_id:
        raise HTTPException(status_code=404, detail="Zoom webhook tenant not found")
    if resolved_tenant_id:
        # Tenant OAuth app secrets are RLS-protected. Bind the validated path or
        # account mapping before reading the secret used to authenticate Zoom.
        await set_tenant_context(db, resolved_tenant_id)
    secret = await get_zoom_phone_webhook_secret(
        db,
        tenant_id=resolved_tenant_id,
    )
    if not secret:
        raise HTTPException(
            status_code=501,
            detail="Zoom webhook secret token is not configured.",
        )

    if event.get("event") == "endpoint.url_validation":
        plain_token = ((event.get("payload") or {}).get("plainToken") or "").strip()
        if not plain_token:
            raise HTTPException(status_code=400, detail="Missing Zoom plainToken")
        return zoom_webhook_validation_response(plain_token, secret=secret)

    signature_ok = verify_zoom_webhook_signature(
        body,
        request.headers.get("x-zm-request-timestamp"),
        request.headers.get("x-zm-signature"),
        secret=secret,
    )
    if not signature_ok:
        raise HTTPException(status_code=401, detail="Invalid Zoom webhook signature")

    if not resolved_tenant_id:
        logger.warning("Zoom Phone webhook ignored: no tenant mapped for event")
        return {"status": "ignored", "reason": "tenant_not_mapped"}

    await set_tenant_context(db, resolved_tenant_id)
    credential = await db.scalar(
        select(TenantCredential).where(
            TenantCredential.tenant_id == uuid.UUID(resolved_tenant_id),
            TenantCredential.provider == ZOOM_PHONE_PROVIDER,
            TenantCredential.is_active,
        )
    )
    oauth_app = await get_tenant_oauth_app(
        db,
        tenant_id=resolved_tenant_id,
        provider=ZOOM_PHONE_PROVIDER,
    )
    app_account_id = _verified_zoom_account_binding(
        oauth_app.zoom_account_id if oauth_app else None
    )
    credential_account_id = _verified_zoom_account_binding(
        credential.service_account_email if credential else None
    )
    event_account_id = (
        str((event.get("payload") or {}).get("account_id") or "").strip()
        if isinstance(event.get("payload"), dict)
        else ""
    )
    if not credential or not oauth_app or not credential.encrypted_refresh_token:
        raise HTTPException(
            status_code=409,
            detail="Zoom Phone must be reconnected before webhook delivery.",
        )
    if (
        not event_account_id
        or event_account_id.isdecimal()
        or not _ZOOM_ACCOUNT_ID_PATTERN.fullmatch(event_account_id)
    ):
        raise HTTPException(status_code=403, detail="Zoom webhook account mismatch")
    if (
        app_account_id
        and credential_account_id
        and not secrets.compare_digest(app_account_id, credential_account_id)
    ):
        raise HTTPException(status_code=403, detail="Zoom webhook account mismatch")
    if (
        app_account_id and not secrets.compare_digest(event_account_id, app_account_id)
    ) or (
        credential_account_id
        and not secrets.compare_digest(event_account_id, credential_account_id)
    ):
        # A known provider binding must match before any event can enter this
        # tenant's durable queue. API history sync remains independently usable.
        raise HTTPException(status_code=403, detail="Zoom webhook account mismatch")

    webhook_verified = bool(app_account_id and credential_account_id)

    if credential.health not in {"healthy", "account_verification_required"}:
        raise HTTPException(
            status_code=409,
            detail="Zoom Phone must be reconnected before webhook delivery.",
        )

    if event.get("event") not in ZOOM_PHONE_HISTORY_COMPLETED_EVENTS:
        return {"status": "ignored", "reason": "event_not_handled"}

    jobs = zoom_phone_webhook_jobs(event)
    if not jobs:
        return {"status": "ignored", "reason": "no_inbound_call_identifiers"}

    terminal_jobs = 0
    for job in jobs:
        job_payload = dict(job.payload)
        if not webhook_verified:
            # Do not persist a webhook binding at receipt time. The durable
            # worker must first fetch this exact call through this tenant's OAuth
            # grant, proving the signed event and grant belong to one account.
            job_payload["account_binding"] = {
                "account_id": event_account_id,
                "proof": "signed_event_exact_call_fetch",
            }
        queued_job = await enqueue_job(
            db,
            tenant_id=resolved_tenant_id,
            kind="zoom_phone_call_import",
            idempotency_key=job.idempotency_key,
            payload=job_payload,
        )
        terminal_jobs += int(queued_job.status == "failed")
    # Zoom receives 2xx only after every provider call identifier is durable.
    # A commit/enqueue failure propagates as 5xx so the provider retries.
    await db.commit()
    response = {"status": "accepted", "queued": len(jobs) - terminal_jobs}
    if terminal_jobs:
        # The event is already durably represented. Avoid an unbounded provider
        # redelivery loop; hourly reconciliation repairs it and only then marks
        # the matching failed job complete.
        response["reconciliation_pending"] = terminal_jobs
    return response


@router.post("/zoom-phone/webhook")
async def zoom_phone_webhook(
    request: Request,
):
    del request
    raise HTTPException(
        status_code=410,
        detail="Use the tenant-specific Zoom Phone webhook URL.",
    )


@router.post("/zoom-phone/webhook/{tenant_id}")
async def zoom_phone_tenant_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _handle_zoom_phone_webhook(request, db, tenant_id=str(tenant_id))


@router.put("/zoom-phone/app-credentials")
async def save_zoom_phone_app_credentials(
    payload: ZoomPhoneAppCredentialRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    tenant_id = str(user.tenant_id)
    await set_tenant_context(db, tenant_id)

    client_id = payload.client_id.strip()
    client_secret = payload.client_secret.strip()
    webhook_secret_token = payload.webhook_secret_token.strip()
    existing_app = await get_tenant_oauth_app(
        db,
        tenant_id=tenant_id,
        provider=ZOOM_PHONE_PROVIDER,
    )
    if not existing_app and (not client_id or not client_secret):
        raise HTTPException(
            status_code=422,
            detail="Zoom OAuth client ID and client secret are required.",
        )
    # ``zoom_account_id`` remains accepted in the request for rolling frontend
    # compatibility, but it is never trusted as an administrator-entered
    # binding. Zoom's human-facing numeric Account Number is not the opaque API
    # account id. Preserve only an existing provider-proven binding.
    zoom_account_id = _verified_zoom_account_binding(
        getattr(existing_app, "zoom_account_id", None)
    )
    if bool(client_id) != bool(client_secret):
        raise HTTPException(
            status_code=422,
            detail="Enter both Zoom OAuth client ID and client secret to replace the saved app.",
        )
    app_changed = existing_app is None
    if existing_app and not client_id and not client_secret:
        if not webhook_secret_token:
            raise HTTPException(
                status_code=422,
                detail="Enter a Zoom webhook secret token to update Zoom Phone setup.",
            )
        if webhook_secret_token:
            existing_app.encrypted_webhook_secret_token = encrypt_token(
                webhook_secret_token
            )
        # Opportunistically remove legacy numeric Account Number values without
        # touching the still-valid OAuth grant.
        existing_app.zoom_account_id = zoom_account_id
        existing_app.configured_by_user_id = user.id
        existing_app.is_active = True
        app = existing_app
        await db.flush()
    else:
        if not webhook_secret_token and not getattr(
            existing_app, "encrypted_webhook_secret_token", None
        ):
            raise HTTPException(
                status_code=422,
                detail="Zoom webhook secret token is required.",
            )
        if existing_app:
            try:
                app_changed = app_changed or not (
                    secrets.compare_digest(
                        decrypt_token(existing_app.encrypted_client_id), client_id
                    )
                    and secrets.compare_digest(
                        decrypt_token(existing_app.encrypted_client_secret),
                        client_secret,
                    )
                )
            except Exception:
                app_changed = True
        app = await upsert_zoom_phone_oauth_app(
            db,
            tenant_id=tenant_id,
            user_id=str(user.id),
            client_id=client_id,
            client_secret=client_secret,
            zoom_account_id=None if app_changed else zoom_account_id,
            webhook_secret_token=webhook_secret_token or None,
            redirect_uri=_zoom_phone_redirect_uri(),
            scopes=settings.ZOOM_PHONE_SCOPES,
        )
    if app_changed:
        credential = await db.scalar(
            select(TenantCredential).where(
                TenantCredential.tenant_id == user.tenant_id,
                TenantCredential.provider == ZOOM_PHONE_PROVIDER,
            )
        )
        if credential:
            credential.is_active = False
            credential.health = "reauthorization_required"
            credential.last_refresh_error = (
                "Zoom OAuth app credentials or account mapping changed; reconnect "
                "is required."
            )
    await db.commit()
    return {
        "status": "saved",
        "app_credentials": _app_credentials_payload(
            app,
            source="tenant",
            platform_ready=False,
        ),
    }


@router.delete("/zoom-phone/app-credentials")
async def clear_zoom_phone_app_credentials(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    tenant_id = str(user.tenant_id)
    await set_tenant_context(db, tenant_id)
    app = await get_tenant_oauth_app(
        db,
        tenant_id=tenant_id,
        provider=ZOOM_PHONE_PROVIDER,
    )
    if app:
        app.is_active = False
    credential = await db.scalar(
        select(TenantCredential).where(
            TenantCredential.tenant_id == user.tenant_id,
            TenantCredential.provider == ZOOM_PHONE_PROVIDER,
        )
    )
    if credential:
        credential.is_active = False
        credential.health = "reauthorization_required"
        credential.last_refresh_error = (
            "Zoom OAuth app credentials were cleared; reconnect is required."
        )
    await db.commit()
    return {
        "status": "cleared",
        "app_credentials": _app_credentials_payload(
            None,
            source=None,
            platform_ready=False,
        ),
    }


# ── Onboarding hook ─────────────────────────────────────────────────────


async def _onboarding_post_connect(
    db: AsyncSession, tenant_id: str, provider: str
) -> None:
    """After admin connects an integration during onboarding, advance the step
    so the background directory sync can run without blocking OAuth redirect.

    Uses an atomic UPDATE … WHERE … RETURNING so that simultaneous connects
    from two providers cannot race on the step counter.
    """
    import logging

    _logger = logging.getLogger(__name__)
    try:
        from sqlalchemy import text as _text

        # Atomically advance from step 1 (consent) to step 2 (syncing).
        # The RETURNING clause gives us the pre-update step so we know
        # whether this call was the one that advanced it.
        result = await db.execute(
            _text(
                """
                UPDATE tenants
                   SET onboarding_step = 2
                 WHERE id = :tid
                   AND onboarding_completed = false
                   AND onboarding_step  < 2
                RETURNING onboarding_step AS old_step
                """
            ),
            {"tid": tenant_id},
        )
        row = result.fetchone()
        if row is not None:
            _logger.info(
                "Onboarding step advanced 1→2 (provider=%s, tenant=%s)",
                provider,
                tenant_id,
            )
    except Exception as exc:
        _logger.warning("Onboarding post-connect hook failed: %s", exc)


def _schedule_user_sync_post_connect(tenant_id: str, provider: str) -> None:
    """Run the best-effort directory sync outside the OAuth redirect response."""
    asyncio.create_task(_sync_users_post_connect(tenant_id, provider))


async def _sync_users_post_connect(tenant_id: str, provider: str) -> None:
    """Best-effort directory sync after admin re-authorization.

    This clears stale failure state immediately when new consent/API settings are
    valid, and records the real provider error when they are not.
    """
    async with async_session_maker() as db:
        await set_tenant_context(db, tenant_id)
        await _sync_users_post_connect_with_session(db, tenant_id, provider)


async def _sync_users_post_connect_with_session(
    db: AsyncSession, tenant_id: str, provider: str
) -> None:
    try:
        from app.services.user_sync import UserSyncService

        sync_svc = UserSyncService()
        if provider == "microsoft":
            await sync_svc.sync_microsoft_users(db, tenant_id)
        elif provider == "google":
            await sync_svc.sync_google_users(db, tenant_id)

        from app.models.tenant import Tenant

        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant and not tenant.onboarding_completed and tenant.onboarding_step < 3:
            tenant.onboarding_step = 3
            await db.commit()
    except Exception as exc:
        logger.warning(
            "Post-connect user sync failed for tenant %s provider=%s: %s",
            tenant_id,
            provider,
            exc,
        )
        try:
            from app.services.user_sync import UserSyncService

            await UserSyncService().record_sync_failure(
                db, tenant_id, provider, str(exc)
            )
        except Exception:
            logger.warning(
                "Failed to record post-connect sync failure for tenant %s provider=%s",
                tenant_id,
                provider,
                exc_info=True,
            )


async def _post_connect_redirect(
    db: AsyncSession, tenant_id: str, provider: str
) -> RedirectResponse:
    """Build the success redirect back into the app after OAuth connect.

    Lands on the onboarding wizard while onboarding is in progress, otherwise
    the admin cloud-search settings tab. The ``connected`` query param is a UX
    hint the frontend can use to refresh integration status.
    """
    from app.models.tenant import Tenant

    completed = False
    try:
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        completed = bool(tenant and tenant.onboarding_completed)
    except Exception:
        pass

    base = settings.FRONTEND_URL.rstrip("/")
    if completed:
        tab = "zoom" if provider in {"zoom", ZOOM_PHONE_PROVIDER} else "cloud-search"
        target = f"{base}/admin?tab={tab}&connected={provider}"
    else:
        target = f"{base}/onboarding?connected={provider}"
    return RedirectResponse(target, status_code=302)


def _error_redirect(provider: str, code: str) -> RedirectResponse:
    """Redirect back to the app's onboarding wizard with an error hint instead
    of stranding the user on a raw-JSON error page."""
    base = settings.FRONTEND_URL.rstrip("/")
    return RedirectResponse(
        f"{base}/onboarding?error={code}&provider={provider}", status_code=302
    )


# ── Status endpoints ─────────────────────────────────────────────────────


@router.get("/status")
async def integration_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> IntegrationsListResponse:
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)

    ms_cred = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == "microsoft",
        )
    )
    ms_row = ms_cred.scalar_one_or_none()

    google_cred = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == "google",
        )
    )
    google_row = google_cred.scalar_one_or_none()

    user_count = await db.execute(
        select(UserOAuthToken).where(UserOAuthToken.tenant_id == tenant_id)
    )
    users_list = user_count.scalars().all()

    ms_required = MICROSOFT_ADMIN_SCOPES
    google_required = GOOGLE_ADMIN_SCOPES

    ms_connected = ms_row is not None and ms_row.is_active
    ms_missing = missing_scopes(
        "microsoft", ms_row.scopes if ms_row else None, ms_required, _scope_is_granted
    )
    ms_teams_missing = missing_teams_scopes(ms_row.scopes if ms_row else None)
    ms_teams_connected = (
        settings.TEAMS_FEATURE_ENABLED and ms_connected and not ms_teams_missing
    )

    ms_status = IntegrationStatus(
        provider="microsoft",
        connected=ms_connected,
        scopes=ms_row.scopes if ms_row else None,
        required_scopes=ms_required,
        missing_scopes=ms_missing,
        health=ms_row.health if ms_row else "disconnected",
        reconnect_required=bool(ms_row and (not ms_row.is_active or ms_missing)),
        last_refresh_at=ms_row.last_refresh_at if ms_row else None,
        last_refresh_error=ms_row.last_refresh_error if ms_row else None,
        scopes_version=ms_row.scopes_version if ms_row else 1,
        expires_at=ms_row.token_expires_at if ms_row else None,
        service_account_email=ms_row.service_account_email if ms_row else None,
        last_user_sync_at=ms_row.last_user_sync_at if ms_row else None,
        last_user_sync_status=ms_row.last_user_sync_status if ms_row else None,
        last_user_sync_error=ms_row.last_user_sync_error if ms_row else None,
        last_user_sync_total=(
            ms_row.last_user_sync_total
            if ms_row and ms_row.last_user_sync_total is not None
            else 0
        ),
        teams_connected=ms_teams_connected,
        teams_missing_scopes=ms_teams_missing,
    )
    google_connected = google_row is not None and google_row.is_active
    google_missing = missing_scopes(
        "google",
        google_row.scopes if google_row else None,
        google_required,
        _scope_is_granted,
    )
    google_status = IntegrationStatus(
        provider="google",
        connected=google_connected,
        scopes=google_row.scopes if google_row else None,
        required_scopes=google_required,
        missing_scopes=google_missing,
        health=google_row.health if google_row else "disconnected",
        reconnect_required=bool(
            google_row and (not google_row.is_active or google_missing)
        ),
        last_refresh_at=google_row.last_refresh_at if google_row else None,
        last_refresh_error=google_row.last_refresh_error if google_row else None,
        scopes_version=google_row.scopes_version if google_row else 1,
        expires_at=google_row.token_expires_at if google_row else None,
        service_account_email=google_row.service_account_email if google_row else None,
        last_user_sync_at=google_row.last_user_sync_at if google_row else None,
        last_user_sync_status=google_row.last_user_sync_status if google_row else None,
        last_user_sync_error=google_row.last_user_sync_error if google_row else None,
        last_user_sync_total=(
            google_row.last_user_sync_total
            if google_row and google_row.last_user_sync_total is not None
            else 0
        ),
    )

    return IntegrationsListResponse(
        microsoft=ms_status,
        google=google_status,
        user_count=len(users_list),
        tenant_credential_count=(1 if ms_row else 0) + (1 if google_row else 0),
    )


# ── Disconnect endpoints ─────────────────────────────────────────────────


async def _revoke_all_provider_tokens(
    db: AsyncSession, tenant_id: str, provider: str
) -> None:
    """Best-effort revoke every tenant + per-user token for a provider before deleting rows."""
    cred_result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == provider,
        )
    )
    user_result = await db.execute(
        select(UserOAuthToken).where(
            UserOAuthToken.tenant_id == tenant_id,
            UserOAuthToken.provider == provider,
        )
    )
    rows = list(cred_result.scalars()) + list(user_result.scalars())
    for row in rows:
        try:
            access_token = (
                decrypt_token(row.encrypted_access_token)
                if row.encrypted_access_token
                else None
            )
            refresh_token = (
                decrypt_token(row.encrypted_refresh_token)
                if row.encrypted_refresh_token
                else None
            )
            await revoke_provider_token(provider, access_token, refresh_token)
        except Exception:
            logger.warning(
                "Failed to revoke %s token for tenant=%s row=%s",
                provider,
                tenant_id,
                getattr(row, "id", None),
                exc_info=True,
            )


@router.post("/microsoft/disconnect")
async def microsoft_disconnect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)
    await _revoke_all_provider_tokens(db, tenant_id, "microsoft")
    await db.execute(
        delete(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == "microsoft",
        )
    )
    await db.execute(
        delete(UserOAuthToken).where(
            UserOAuthToken.tenant_id == tenant_id,
            UserOAuthToken.provider == "microsoft",
        )
    )
    await db.commit()

    return {"status": "disconnected", "provider": "microsoft"}


@router.post("/google/disconnect")
async def google_disconnect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)
    await _revoke_all_provider_tokens(db, tenant_id, "google")
    await db.execute(
        delete(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == "google",
        )
    )
    await db.execute(
        delete(UserOAuthToken).where(
            UserOAuthToken.tenant_id == tenant_id,
            UserOAuthToken.provider == "google",
        )
    )
    await db.commit()

    return {"status": "disconnected", "provider": "google"}


@router.get("/zoom/status")
async def zoom_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    tenant_id = str(user.tenant_id)
    await set_tenant_context(db, tenant_id)

    user_result = await db.execute(
        select(UserOAuthToken).where(
            UserOAuthToken.user_id == user.id,
            UserOAuthToken.tenant_id == user.tenant_id,
            UserOAuthToken.provider == "zoom",
        )
    )
    user_row = user_result.scalar_one_or_none()
    tenant_result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == user.tenant_id,
            TenantCredential.provider == "zoom",
            TenantCredential.is_active,
        )
    )
    tenant_row = tenant_result.scalar_one_or_none()
    row = user_row or tenant_row
    return {
        "configured": bool(settings.ZOOM_CLIENT_ID and settings.ZOOM_CLIENT_SECRET),
        "connected": bool(row),
        "connection_type": "user" if user_row else "tenant" if tenant_row else None,
        "expires_at": row.token_expires_at.isoformat()
        if row and row.token_expires_at
        else None,
        "scopes": row.scopes.split() if row and row.scopes else [],
    }


@router.get("/zoom-phone/status")
async def zoom_phone_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    tenant_id = str(user.tenant_id)
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == user.tenant_id,
            TenantCredential.provider == ZOOM_PHONE_PROVIDER,
        )
    )
    row = result.scalar_one_or_none()
    app = await get_tenant_oauth_app(
        db,
        tenant_id=tenant_id,
        provider=ZOOM_PHONE_PROVIDER,
    )
    missing = _missing_scopes(
        "zoom",
        row.scopes if row else None,
        settings.ZOOM_PHONE_SCOPES,
    )
    provider_missing_scopes = bool(row and row.health == "missing_scopes")
    if provider_missing_scopes and not missing:
        # Zoom can reject code 104 even when its token response claimed every
        # requested scope. Surface the actual provider verdict to the admin.
        missing = settings.ZOOM_PHONE_SCOPES.split()
    platform_ready = False
    app_account_id = _verified_zoom_account_binding(
        app.zoom_account_id if app else None
    )
    credential_account_id = _verified_zoom_account_binding(
        row.service_account_email if row else None
    )
    tenant_app_ready = bool(app)
    configured = tenant_app_ready
    api_health = (
        "healthy"
        if row and row.health == "account_verification_required"
        else row.health
        if row
        else "disconnected"
    )
    connected = bool(
        row
        and row.is_active
        and row.encrypted_refresh_token
        and api_health == "healthy"
    )
    app_source = "tenant" if tenant_app_ready else None
    webhook_secret_configured = bool(
        getattr(app, "encrypted_webhook_secret_token", None)
    )
    webhook_verified = bool(
        app_account_id
        and credential_account_id
        and secrets.compare_digest(app_account_id, credential_account_id)
    )
    webhook_status = (
        "verified"
        if webhook_verified
        else "pending"
        if webhook_secret_configured
        else "not_configured"
    )
    status_payload = {
        "configured": configured,
        "connected": connected,
        "provider": ZOOM_PHONE_PROVIDER,
        "app_source": app_source,
        "tenant_app_configured": tenant_app_ready,
        "zoom_account_id_configured": bool(app_account_id),
        # Compatibility field for older clients. Webhook binding is no longer
        # permitted to block API sync or Test Connection.
        "account_verification_required": False,
        "webhook_verified": webhook_verified,
        "webhook_status": webhook_status,
        "platform_app_configured": platform_ready,
        "redirect_uri": _zoom_phone_redirect_uri(),
        "webhook_url": _zoom_phone_webhook_uri(tenant_id),
        "webhook_secret_configured": webhook_secret_configured,
        "app_credentials": _app_credentials_payload(
            app,
            source=app_source,
            platform_ready=platform_ready,
        ),
        "required_scopes": settings.ZOOM_PHONE_SCOPES.split(),
        "missing_scopes": missing,
        "scopes": row.scopes.split() if row and row.scopes else [],
        "expires_at": row.token_expires_at.isoformat()
        if row and row.token_expires_at
        else None,
        "health": api_health,
        "reconnect_required": bool(
            row and (not connected or provider_missing_scopes or bool(missing))
        ),
        "status": (
            "not_configured"
            if not configured
            else "missing_scopes"
            if provider_missing_scopes or missing
            else "reauthorization_required"
            if row and not connected
            else "not_connected"
            if not connected
            else "connected"
        ),
    }
    if user.role != "admin":
        # Reception staff only need connection health for the intake feed.
        # Keep OAuth app metadata, callback URLs, scopes, and credential hints
        # confined to tenant administrators.
        return {
            "configured": status_payload["configured"],
            "connected": status_payload["connected"],
            "provider": status_payload["provider"],
            "status": status_payload["status"],
        }
    return status_payload


@router.post("/zoom-phone/test")
async def zoom_phone_test(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    tenant_id = str(user.tenant_id)
    await set_tenant_context(db, tenant_id)
    try:
        result = await probe_zoom_phone_connection(db, tenant_id=tenant_id)
        await db.commit()
        return {"status": "ok", **result}
    except ZoomPhoneIntegrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/zoom-phone/disconnect")
async def zoom_phone_disconnect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    tenant_id = str(user.tenant_id)
    await set_tenant_context(db, tenant_id)
    await db.execute(
        delete(TenantCredential).where(
            TenantCredential.tenant_id == user.tenant_id,
            TenantCredential.provider == ZOOM_PHONE_PROVIDER,
        )
    )
    await db.commit()
    return {"status": "disconnected", "provider": ZOOM_PHONE_PROVIDER}


@router.post("/zoom/disconnect")
async def zoom_disconnect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    tenant_id = str(user.tenant_id)
    await set_tenant_context(db, tenant_id)
    if user.role == "admin":
        await db.execute(
            delete(TenantCredential).where(
                TenantCredential.tenant_id == user.tenant_id,
                TenantCredential.provider == "zoom",
            )
        )
    await db.execute(
        delete(UserOAuthToken).where(
            UserOAuthToken.tenant_id == user.tenant_id,
            UserOAuthToken.user_id == user.id,
            UserOAuthToken.provider == "zoom",
        )
    )
    await db.commit()
    return {"status": "disconnected", "provider": "zoom"}


# ── Cloud-init helpers & retry ────────────────────────────────────────────


async def _ensure_cloud_root(db: AsyncSession, tenant_id: str) -> None:
    """Create or repair tenant cloud root folder records.

    Called after every admin OAuth connect so that re-authorizing with the
    correct scopes automatically repairs a previously broken cloud setup.
    """
    import logging as _log

    _logger = _log.getLogger(__name__)
    try:
        from app.models.tenant import Tenant
        from app.services.cloud_init import initialize_cloud_root_folder

        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if not tenant:
            return

        existing = tenant.cloud_root_folder or {}
        fresh = await initialize_cloud_root_folder(db, tenant_id)
        if fresh:
            tenant.cloud_root_folder = {**existing, **fresh}
            await db.commit()
            _logger.info(
                "Auto-repaired cloud root folder for tenant %s on re-auth", tenant_id
            )
    except Exception as exc:
        _logger.warning("_ensure_cloud_root failed for tenant %s: %s", tenant_id, exc)


@router.post("/cloud-init/retry")
async def cloud_init_retry(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Re-create missing cloud folders for this tenant.

    Creates the root 'claritylegal-records' folder if absent, then backfills
    missing matter subfolders for every matter. Safe to call multiple times:
    existing folders are detected and reused.
    """
    from app.models.plugin import Matter
    from app.models.tenant import Tenant
    from app.services.cloud_init import (
        initialize_cloud_root_folder,
        initialize_matter_folders,
    )

    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)

    # 1. Ensure root folder exists
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    cloud_root = tenant.cloud_root_folder or {}
    try:
        fresh = await initialize_cloud_root_folder(db, str(tenant_id))
        if fresh:
            cloud_root = {**cloud_root, **fresh}
            tenant.cloud_root_folder = cloud_root
            await db.commit()
    except Exception as exc:
        logger.warning("cloud_init_retry: root folder init failed: %s", exc)

    if not cloud_root:
        return {
            "root": None,
            "matters_initialized": 0,
            "matters_failed": 0,
            "error": "No cloud credentials available — connect Google or Microsoft first",
        }

    # 2. Backfill matter folders
    matters_result = await db.execute(
        select(Matter).where(
            Matter.tenant_id == tenant_id,
        )
    )
    matters = matters_result.scalars().all()

    initialized = 0
    failed = 0
    for matter in matters:
        slug = getattr(matter, "slug", None) or str(matter.id)
        try:
            folder = await initialize_matter_folders(
                db=db,
                tenant_id=str(tenant_id),
                matter_slug=slug,
                cloud_root=cloud_root,
            )
            if folder:
                matter.cloud_folder = {**(matter.cloud_folder or {}), **folder}
                initialized += 1
            elif not matter.cloud_folder:
                failed += 1
        except Exception as exc:
            logger.warning(
                "cloud_init_retry: matter %s folder init failed: %s", matter.id, exc
            )
            failed += 1

    await db.commit()

    return {
        "root": cloud_root,
        "root_providers": sorted(cloud_root.keys()),
        "matters_checked": len(matters),
        "matters_initialized": initialized,
        "matters_failed": failed,
    }
