import json as _json
import secrets
import time as _time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query
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

MICROSOFT_ADMIN_SCOPES = "offline_access User.Read.All Mail.Read Files.Read.All Sites.Read.All Calendars.ReadWrite"
GOOGLE_ADMIN_SCOPES = (
    "https://www.googleapis.com/auth/admin.directory.user.readonly "
    "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/calendar "
    "https://www.googleapis.com/auth/drive.readonly"
)


@router.get("/microsoft/connect")
async def microsoft_connect(
    request: Request,
    intent: str = Query("admin", description="admin=tenant-wide, user=per-user"),
):
    state = secrets.token_urlsafe(32)
    await _save_state(request, state, {"intent": intent, "provider": "microsoft"})

    ms_tenant = settings.MICROSOFT_TENANT_ID
    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/microsoft/callback"
    scopes = MICROSOFT_ADMIN_SCOPES if intent == "admin" else "offline_access Mail.Read Files.Read.All Calendars.ReadWrite"

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
    return {"redirect_url": authorize_url}


@router.get("/microsoft/callback")
async def microsoft_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, meta = await _consume_state(request, state)
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

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
                "scope": MICROSOFT_ADMIN_SCOPES if intent == "admin" else "offline_access Mail.Read Files.Read.All",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Token exchange failed: {token_resp.text[:200]}",
            )

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)
        scope_str = token_data.get("scope", "")

        if not access_token:
            raise HTTPException(status_code=400, detail="No access token received")

        if intent == "admin":
            user_id = getattr(request.state, "user_id", None)
            tenant_id = getattr(request.state, "tenant_id", None)
            if not tenant_id:
                profile_resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if profile_resp.status_code == 200:
                    profile = profile_resp.json()
                    email = (profile.get("mail") or profile.get("userPrincipalName") or "").lower()
                    from app.models.user import User
                    result = await db.execute(select(User).where(User.email == email))
                    user_row = result.scalar_one_or_none()
                    if user_row:
                        user_id = str(user_row.id)
                        tenant_id = str(user_row.tenant_id)

            if tenant_id:
                await set_tenant_context(db, tenant_id)
                result = await db.execute(
                    select(TenantCredential).where(
                        TenantCredential.tenant_id == tenant_id,
                        TenantCredential.provider == "microsoft",
                    )
                )
                existing = result.scalar_one_or_none()
                async with db.begin():
                    if existing:
                        existing.encrypted_access_token = encrypt_token(access_token)
                        existing.encrypted_refresh_token = encrypt_token(refresh_token) if refresh_token else None
                        existing.token_expires_at = _time.time() + expires_in
                        existing.scopes = scope_str
                        existing.is_active = True
                    else:
                        db.add(
                            TenantCredential(
                                tenant_id=uuid.UUID(tenant_id),
                                provider="microsoft",
                                encrypted_access_token=encrypt_token(access_token),
                                encrypted_refresh_token=encrypt_token(refresh_token) if refresh_token else None,
                                token_expires_at=_time.time() + expires_in,
                                scopes=scope_str,
                            )
                        )
        else:
            user_id = getattr(request.state, "user_id", None)
            tenant_id = getattr(request.state, "tenant_id", None)
            if user_id and tenant_id:
                await set_tenant_context(db, tenant_id)
                existing = await db.execute(
                    select(UserOAuthToken).where(
                        UserOAuthToken.user_id == user_id,
                        UserOAuthToken.provider == "microsoft",
                    )
                )
                row = existing.scalar_one_or_none()
                async with db.begin():
                    if row:
                        row.encrypted_access_token = encrypt_token(access_token)
                        row.encrypted_refresh_token = encrypt_token(refresh_token) if refresh_token else None
                        row.token_expires_at = _time.time() + expires_in
                        row.scopes = scope_str
                    else:
                        db.add(
                            UserOAuthToken(
                                user_id=uuid.UUID(user_id),
                                tenant_id=uuid.UUID(tenant_id),
                                provider="microsoft",
                                encrypted_access_token=encrypt_token(access_token),
                                encrypted_refresh_token=encrypt_token(refresh_token) if refresh_token else None,
                                token_expires_at=_time.time() + expires_in,
                                scopes=scope_str,
                            )
                        )

    return {"status": "connected", "provider": "microsoft", "scopes": scope_str}


@router.get("/google/connect")
async def google_connect(
    request: Request,
    intent: str = Query("admin", description="admin=tenant-wide, user=per-user"),
):
    state = secrets.token_urlsafe(32)
    await _save_state(request, state, {"intent": intent, "provider": "google"})

    redirect_uri = f"{settings.BACKEND_URL}/api/integrations/google/callback"
    scopes = GOOGLE_ADMIN_SCOPES if intent == "admin" else "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar"

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
    return {"redirect_url": authorize_url}


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    valid, meta = await _consume_state(request, state)
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

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
            raise HTTPException(
                status_code=400,
                detail=f"Token exchange failed: {token_resp.text[:200]}",
            )

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)
        scope_str = token_data.get("scope", "")

        if not access_token:
            raise HTTPException(status_code=400, detail="No access token received")

        if intent == "admin":
            tenant_id = getattr(request.state, "tenant_id", None)
            if not tenant_id:
                userinfo_resp = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if userinfo_resp.status_code == 200:
                    profile = userinfo_resp.json()
                    email = (profile.get("email") or "").lower()
                    from app.models.user import User
                    result = await db.execute(select(User).where(User.email == email))
                    user_row = result.scalar_one_or_none()
                    if user_row:
                        tenant_id = str(user_row.tenant_id)

            if tenant_id:
                await set_tenant_context(db, tenant_id)
                existing = await db.execute(
                    select(TenantCredential).where(
                        TenantCredential.tenant_id == tenant_id,
                        TenantCredential.provider == "google",
                    )
                )
                row = existing.scalar_one_or_none()
                async with db.begin():
                    if row:
                        row.encrypted_access_token = encrypt_token(access_token)
                        row.encrypted_refresh_token = encrypt_token(refresh_token) if refresh_token else None
                        row.token_expires_at = _time.time() + expires_in
                        row.scopes = scope_str
                        row.is_active = True
                    else:
                        db.add(
                            TenantCredential(
                                tenant_id=uuid.UUID(tenant_id),
                                provider="google",
                                encrypted_access_token=encrypt_token(access_token),
                                encrypted_refresh_token=encrypt_token(refresh_token) if refresh_token else None,
                                token_expires_at=_time.time() + expires_in,
                                scopes=scope_str,
                            )
                        )
        else:
            user_id = getattr(request.state, "user_id", None)
            tenant_id = getattr(request.state, "tenant_id", None)
            if user_id and tenant_id:
                await set_tenant_context(db, tenant_id)
                existing = await db.execute(
                    select(UserOAuthToken).where(
                        UserOAuthToken.user_id == user_id,
                        UserOAuthToken.provider == "google",
                    )
                )
                row = existing.scalar_one_or_none()
                async with db.begin():
                    if row:
                        row.encrypted_access_token = encrypt_token(access_token)
                        row.encrypted_refresh_token = encrypt_token(refresh_token) if refresh_token else None
                        row.token_expires_at = _time.time() + expires_in
                        row.scopes = scope_str
                    else:
                        db.add(
                            UserOAuthToken(
                                user_id=uuid.UUID(user_id),
                                tenant_id=uuid.UUID(tenant_id),
                                provider="google",
                                encrypted_access_token=encrypt_token(access_token),
                                encrypted_refresh_token=encrypt_token(refresh_token) if refresh_token else None,
                                token_expires_at=_time.time() + expires_in,
                                scopes=scope_str,
                            )
                        )

    return {"status": "connected", "provider": "google", "scopes": scope_str}


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
