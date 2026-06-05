import base64
import json as _json
import secrets
import time as _time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.tenant_credential import TenantCredential
from app.models.user_oauth_token import UserOAuthToken
from app.schemas.integrations import IntegrationStatus, IntegrationsListResponse
from app.services.token_vault import encrypt_token

settings = get_settings()
router = APIRouter(prefix="/api/integrations", tags=["integrations"])

_STATE_TTL = 600
_fallback_states: dict[str, float] = {}
_fallback_state_data: dict[str, dict] = {}


async def _save_state(request: Request, state: str, data: dict | None = None) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.setex(f"integration:state:{state}", _STATE_TTL, "1")
        if data:
            await redis.setex(
                f"integration:statedata:{state}", _STATE_TTL, _json.dumps(data)
            )
    else:
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

MICROSOFT_ADMIN_SCOPES = "offline_access User.Read.All Mail.Read Files.ReadWrite.All Sites.Read.All Calendars.ReadWrite"
GOOGLE_ADMIN_SCOPES = (
    "openid email profile "
    "https://www.googleapis.com/auth/admin.directory.user.readonly "
    "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/calendar "
    "https://www.googleapis.com/auth/drive"
)


def _expires_at(expires_in: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


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


@router.get("/microsoft/connect")
async def microsoft_connect(
    request: Request,
    intent: str = Query("admin", description="admin=tenant-wide, user=per-user"),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if intent == "admin" and user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    state = secrets.token_urlsafe(32)
    await _save_state(
        request,
        state,
        {
            "intent": intent,
            "provider": "microsoft",
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
        },
    )

    ms_tenant = settings.MICROSOFT_TENANT_ID
    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/microsoft/callback"
    scopes = (
        MICROSOFT_ADMIN_SCOPES
        if intent == "admin"
        else "offline_access Mail.Read Files.Read.All Calendars.ReadWrite"
    )

    authorize_url = (
        f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/authorize"
        f"?client_id={settings.MICROSOFT_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes.replace(' ', '+')}"
        f"&state={state}"
        f"&response_mode=query"
        f"&prompt=consent"
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

    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/microsoft/callback"
    ms_tenant = settings.MICROSOFT_TENANT_ID
    token_url = f"https://login.microsoftonline.com/{ms_tenant}/oauth2/v2.0/token"

    intent = meta.get("intent", "user") if meta else "user"

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            token_url,
            data={
                "client_id": settings.MICROSOFT_CLIENT_ID,
                "client_secret": settings.MICROSOFT_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": MICROSOFT_ADMIN_SCOPES
                if intent == "admin"
                else "offline_access Mail.Read Files.Read.All",
            },
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
            else:
                db.add(
                    TenantCredential(
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
                )
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
                db.add(
                    UserOAuthToken(
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
                )

        await db.commit()

        # Onboarding hook: if admin just connected an integration during onboarding,
        # auto-advance step and trigger user sync
        if intent == "admin":
            await _onboarding_post_connect(db, tenant_id, "microsoft")
            await _ensure_cloud_root(db, tenant_id)

    return await _post_connect_redirect(db, tenant_id, "microsoft")


@router.get("/google/connect")
async def google_connect(
    request: Request,
    intent: str = Query("admin", description="admin=tenant-wide, user=per-user"),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    if intent == "admin" and user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    state = secrets.token_urlsafe(32)
    await _save_state(
        request,
        state,
        {
            "intent": intent,
            "provider": "google",
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
        },
    )

    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/google/callback"
    scopes = (
        GOOGLE_ADMIN_SCOPES
        if intent == "admin"
        else "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar"
    )

    authorize_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes.replace(' ', '+')}"
        "&access_type=offline"
        "&prompt=consent"
        f"&state={state}"
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

    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/google/callback"
    intent = meta.get("intent", "user") if meta else "user"

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
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
                    import base64
                    import json as _json

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
                db.add(
                    TenantCredential(
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
                )
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
                db.add(
                    UserOAuthToken(
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
                )

        await db.commit()

        # Onboarding hook: if admin just connected an integration during onboarding,
        # auto-advance step and trigger user sync
        if intent == "admin":
            await _onboarding_post_connect(db, tenant_id, "google")
            await _ensure_cloud_root(db, tenant_id)

    return await _post_connect_redirect(db, tenant_id, "google")


# ── Onboarding hook ─────────────────────────────────────────────────────


async def _onboarding_post_connect(
    db: AsyncSession, tenant_id: str, provider: str
) -> None:
    """After admin connects an integration during onboarding, advance the step
    and auto-trigger user directory sync."""
    import logging

    _logger = logging.getLogger(__name__)
    try:
        from sqlalchemy import select as _sel

        from app.models.tenant import Tenant

        tenant_result = await db.execute(_sel(Tenant).where(Tenant.id == tenant_id))
        tenant = tenant_result.scalar_one_or_none()
        if not tenant or tenant.onboarding_completed:
            return

        # Advance from step 1 (consent) to step 2 (syncing)
        if tenant.onboarding_step < 2:
            tenant.onboarding_step = 2

            # Auto-trigger user directory sync
            try:
                from app.services.user_sync import UserSyncService

                sync_svc = UserSyncService()
                if provider == "microsoft":
                    await sync_svc.sync_microsoft_users(db, tenant_id)
                elif provider == "google":
                    await sync_svc.sync_google_users(db, tenant_id)

                # After sync, advance to step 3 (review)
                tenant.onboarding_step = 3
            except Exception as sync_err:
                _logger.warning(
                    "Auto user sync failed during onboarding for tenant %s: %s",
                    tenant_id,
                    sync_err,
                )
                # Stay at step 2 so admin can retry

            await db.commit()
    except Exception as exc:
        _logger.warning("Onboarding post-connect hook failed: %s", exc)


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
        target = f"{base}/admin?tab=cloud-search&connected={provider}"
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

    ms_status = IntegrationStatus(
        provider="microsoft",
        connected=ms_row is not None and ms_row.is_active,
        scopes=ms_row.scopes if ms_row else None,
        expires_at=ms_row.token_expires_at if ms_row else None,
    )
    google_status = IntegrationStatus(
        provider="google",
        connected=google_row is not None and google_row.is_active,
        scopes=google_row.scopes if google_row else None,
        expires_at=google_row.token_expires_at if google_row else None,
    )

    return IntegrationsListResponse(
        microsoft=ms_status,
        google=google_status,
        user_count=len(users_list),
        tenant_credential_count=(1 if ms_row else 0) + (1 if google_row else 0),
    )


# ── Disconnect endpoints ─────────────────────────────────────────────────


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


# ── Cloud-init helpers & retry ────────────────────────────────────────────


async def _ensure_cloud_root(db: AsyncSession, tenant_id: str) -> None:
    """If the tenant has no cloud root folder yet, try to create it now.

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
        if tenant.cloud_root_folder:
            return  # already set — nothing to do

        cloud_root = await initialize_cloud_root_folder(db, tenant_id)
        if cloud_root:
            tenant.cloud_root_folder = cloud_root
            await db.commit()
            _logger.info(
                "Auto-created cloud root folder for tenant %s on re-auth", tenant_id
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
    matter subfolders for every matter that has cloud_folder=null.  Safe to
    call multiple times — existing folders are detected and reused.
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
            cloud_root = fresh
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
            Matter.cloud_folder.is_(None),
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
                matter.cloud_folder = folder
                initialized += 1
        except Exception as exc:
            logger.warning(
                "cloud_init_retry: matter %s folder init failed: %s", matter.id, exc
            )
            failed += 1

    await db.commit()

    return {
        "root": cloud_root,
        "matters_initialized": initialized,
        "matters_failed": failed,
    }
