"""QuickBooks Online OAuth2 integration router.

QBO uses OAuth 2.0 with the following flow:
1. GET /connect → redirect to Intuit's authorization page
2. GET /callback → Intuit redirects here with code + realmId
3. Token exchange + encrypted storage (same pattern as TenantCredential)
4. GET /status → connection health
5. POST /disconnect → revoke tokens
"""

import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.qbo import QBOIntegration, QBOItemMapping
from app.schemas.qbo import (
    QBOIntegrationStatus,
    QBOIntegrationResponse,
    QBOIntegrationUpdate,
    QBOItemOption,
    QBOItemMappingResponse,
    QBOItemMappingUpsert,
    QBOSyncStatus,
)
from app.services.token_vault import encrypt_token, decrypt_token

settings = get_settings()
router = APIRouter(prefix="/api/integrations/qbo", tags=["integrations", "qbo"])
logger = logging.getLogger(__name__)

_STATE_TTL = 600
_fallback_states: dict[str, float] = {}
_fallback_state_data: dict[str, dict] = {}

# QBO OAuth endpoints
QBO_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QBO_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QBO_REVOKE_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/revoke"
QBO_SANDBOX_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"

QBO_SCOPES = "com.intuit.quickbooks.accounting openid profile email"


async def _save_state(request: Request, state: str, data: dict) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis:
        import json as _json

        await redis.setex(f"qbo:state:{state}", _STATE_TTL, "1")
        await redis.setex(f"qbo:statedata:{state}", _STATE_TTL, _json.dumps(data))
    else:
        now = time.time()
        # Evict expired entries to prevent unbounded growth
        expired = [k for k, ts in _fallback_states.items() if now - ts > _STATE_TTL]
        for k in expired:
            _fallback_states.pop(k, None)
            _fallback_state_data.pop(k, None)
        _fallback_states[state] = now
        _fallback_state_data[state] = data


async def _consume_state(request: Request, state: str) -> tuple[bool, dict | None]:
    redis = getattr(request.app.state, "redis", None)
    data = None
    if redis:
        import json as _json

        deleted = await redis.delete(f"qbo:state:{state}")
        if deleted:
            raw = await redis.get(f"qbo:statedata:{state}")
            if raw:
                data = _json.loads(raw)
            await redis.delete(f"qbo:statedata:{state}")
        return bool(deleted), data
    ts = _fallback_states.pop(state, None)
    if ts is None:
        return False, None
    data = _fallback_state_data.pop(state, None)
    if time.time() - ts > _STATE_TTL:
        return False, None
    return True, data


async def _get_qbo_integration(
    db: AsyncSession, tenant_id: str
) -> QBOIntegration | None:
    result = await db.execute(
        select(QBOIntegration).where(QBOIntegration.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def _get_fresh_qbo_token(db: AsyncSession, tenant_id: str) -> str | None:
    """Get a valid QBO access token, refreshing if needed."""
    await set_tenant_context(db, tenant_id)
    qbo = await _get_qbo_integration(db, tenant_id)
    if not qbo or not qbo.encrypted_access_token or not qbo.is_active:
        return None

    if qbo.token_expires_at:
        if qbo.token_expires_at.tzinfo is None:
            qbo.token_expires_at = qbo.token_expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < qbo.token_expires_at - timedelta(seconds=60):
            return decrypt_token(qbo.encrypted_access_token)

    # Token expired — refresh
    if not qbo.encrypted_refresh_token:
        return None

    refresh_token = decrypt_token(qbo.encrypted_refresh_token)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            QBO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if resp.status_code != 200:
            qbo.is_active = False
            qbo.last_sync_status = "failed"
            qbo.last_sync_error = f"Token refresh failed: {resp.text[:200]}"
            await db.commit()
            return None

        data = resp.json()
        new_access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 3600)

        if not new_access_token:
            return None

        qbo.encrypted_access_token = encrypt_token(new_access_token)
        if new_refresh_token:
            qbo.encrypted_refresh_token = encrypt_token(new_refresh_token)
        qbo.token_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=expires_in
        )
        await db.commit()

        return new_access_token


def _basic_auth_header() -> str:
    import base64

    creds = f"{settings.QBO_CLIENT_ID}:{settings.QBO_CLIENT_SECRET}"
    return f"Basic {base64.b64encode(creds.encode()).decode()}"


# ── Connect ─────────────────────────────────────────────────────────────────


@router.get("/connect")
async def qbo_connect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Initiate QBO OAuth2 authorization flow."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if not settings.QBO_CLIENT_ID:
        raise HTTPException(status_code=501, detail="QBO_CLIENT_ID not configured")

    state = secrets.token_urlsafe(32)
    await _save_state(
        request,
        state,
        {
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
        },
    )

    redirect_uri = (
        settings.QBO_REDIRECT_URI
        or f"{settings.BACKEND_URL}/api/integrations/qbo/callback"
    )
    authorize_url = (
        f"{QBO_AUTH_URL}"
        f"?client_id={settings.QBO_CLIENT_ID}"
        f"&response_type=code"
        f"&scope={QBO_SCOPES.replace(' ', '+')}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return {"redirect_url": authorize_url}


# ── Callback ────────────────────────────────────────────────────────────────


@router.get("/callback")
async def qbo_callback(
    code: str,
    state: str,
    realmId: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle QBO OAuth2 callback — exchange code for tokens."""
    valid, meta = await _consume_state(request, state)
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    tenant_id = meta.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Missing tenant context in state")

    redirect_uri = (
        settings.QBO_REDIRECT_URI
        or f"{settings.BACKEND_URL}/api/integrations/qbo/callback"
    )

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            QBO_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"QBO token exchange failed: {token_resp.text[:200]}",
            )

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)

        if not access_token:
            raise HTTPException(
                status_code=400, detail="No access token received from QBO"
            )

        await set_tenant_context(db, tenant_id)
        existing = await _get_qbo_integration(db, tenant_id)

        if existing:
            existing.qbo_realm_id = realmId
            existing.encrypted_access_token = encrypt_token(access_token)
            existing.encrypted_refresh_token = (
                encrypt_token(refresh_token) if refresh_token else None
            )
            existing.token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=expires_in
            )
            existing.scopes = QBO_SCOPES
            existing.is_active = True
            existing.sandbox_mode = settings.QBO_ENVIRONMENT == "sandbox"
        else:
            db.add(
                QBOIntegration(
                    tenant_id=uuid.UUID(tenant_id),
                    qbo_realm_id=realmId,
                    encrypted_access_token=encrypt_token(access_token),
                    encrypted_refresh_token=encrypt_token(refresh_token)
                    if refresh_token
                    else None,
                    token_expires_at=datetime.now(timezone.utc)
                    + timedelta(seconds=expires_in),
                    scopes=QBO_SCOPES,
                    is_active=True,
                    sandbox_mode=settings.QBO_ENVIRONMENT == "sandbox",
                )
            )

        await db.commit()

    return {"status": "connected", "provider": "qbo", "realm_id": realmId}


# ── Status ──────────────────────────────────────────────────────────────────


@router.get("/status")
async def qbo_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> QBOIntegrationStatus:
    """Return QBO connection status for the current tenant."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    await set_tenant_context(db, str(user.tenant_id))
    qbo = await _get_qbo_integration(db, str(user.tenant_id))

    if not qbo:
        return QBOIntegrationStatus(
            connected=False,
            sandbox_mode=True,
            is_active=False,
            sync_frequency_minutes=15,
        )

    return QBOIntegrationStatus(
        connected=qbo.is_active and qbo.encrypted_access_token is not None,
        qbo_realm_id=qbo.qbo_realm_id,
        sandbox_mode=qbo.sandbox_mode,
        scopes=qbo.scopes,
        token_expires_at=qbo.token_expires_at,
        is_active=qbo.is_active,
        sync_frequency_minutes=qbo.sync_frequency_minutes,
        last_sync_at=qbo.last_sync_at,
        last_sync_status=qbo.last_sync_status,
        last_sync_error=qbo.last_sync_error,
    )


# ── Update settings ─────────────────────────────────────────────────────────


@router.put("/settings")
async def qbo_update_settings(
    body: QBOIntegrationUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> QBOIntegrationResponse:
    """Update QBO integration settings (sync frequency, sandbox mode)."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    await set_tenant_context(db, str(user.tenant_id))
    qbo = await _get_qbo_integration(db, str(user.tenant_id))
    if not qbo:
        raise HTTPException(status_code=404, detail="QBO integration not configured")

    if body.sync_frequency_minutes is not None:
        qbo.sync_frequency_minutes = body.sync_frequency_minutes
    if body.sandbox_mode is not None:
        qbo.sandbox_mode = body.sandbox_mode

    await db.commit()
    await db.refresh(qbo)

    return QBOIntegrationResponse.model_validate(qbo)


# ── Disconnect ──────────────────────────────────────────────────────────────


@router.post("/disconnect")
async def qbo_disconnect(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Disconnect QBO integration — revoke tokens and deactivate."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    await set_tenant_context(db, str(user.tenant_id))
    qbo = await _get_qbo_integration(db, str(user.tenant_id))
    if not qbo:
        raise HTTPException(status_code=404, detail="QBO integration not configured")

    # Try to revoke the token at Intuit
    if qbo.encrypted_refresh_token:
        try:
            refresh_token = decrypt_token(qbo.encrypted_refresh_token)
            async with httpx.AsyncClient() as client:
                await client.post(
                    QBO_REVOKE_URL,
                    data={"token": refresh_token},
                    headers={
                        "Authorization": _basic_auth_header(),
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
        except Exception:
            logger.warning(f"Failed to revoke QBO token for tenant {user.tenant_id}")

    qbo.encrypted_access_token = None
    qbo.encrypted_refresh_token = None
    qbo.token_expires_at = None
    qbo.is_active = False
    qbo.qbo_realm_id = None
    await db.commit()

    return {"status": "disconnected", "provider": "qbo"}


# ── QBO Item catalogue ───────────────────────────────────────────────────────


@router.get("/items")
async def qbo_list_items(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[QBOItemOption]:
    """Return all active Service-type Items from the connected QBO company."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    await set_tenant_context(db, str(user.tenant_id))
    access_token = await _get_fresh_qbo_token(db, str(user.tenant_id))
    if not access_token:
        raise HTTPException(
            status_code=400, detail="QBO not connected or token expired"
        )

    qbo = await _get_qbo_integration(db, str(user.tenant_id))
    if not qbo or not qbo.qbo_realm_id:
        raise HTTPException(status_code=400, detail="QBO realm ID not found")

    sandbox = qbo.sandbox_mode
    base = (
        "https://sandbox-quickbooks.api.intuit.com"
        if sandbox
        else "https://quickbooks.api.intuit.com"
    )
    url = f"{base}/v3/company/{qbo.qbo_realm_id}/query"
    query = "SELECT * FROM Item WHERE Type = 'Service' AND Active = true MAXRESULTS 200"

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            url,
            params={"query": query},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )

    if resp.status_code != 200:
        logger.warning(f"QBO items fetch failed: {resp.status_code} {resp.text[:200]}")
        raise HTTPException(status_code=502, detail="Failed to fetch QBO items")

    items = resp.json().get("QueryResponse", {}).get("Item", [])
    return [QBOItemOption(id=it["Id"], name=it["Name"]) for it in items]


# ── Item Mappings ─────────────────────────────────────────────────────────────


@router.get("/mappings")
async def qbo_get_mappings(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[QBOItemMappingResponse]:
    """List all billing-type → QBO item mappings for this tenant."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(QBOItemMapping).where(QBOItemMapping.tenant_id == user.tenant_id)
    )
    return [QBOItemMappingResponse.model_validate(m) for m in result.scalars().all()]


@router.put("/mappings")
async def qbo_upsert_mapping(
    body: QBOItemMappingUpsert,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> QBOItemMappingResponse:
    """Create or update a billing-type → QBO Item mapping."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(QBOItemMapping).where(
            QBOItemMapping.tenant_id == user.tenant_id,
            QBOItemMapping.source_type == body.source_type,
            QBOItemMapping.expense_category == body.expense_category,
        )
    )
    mapping = result.scalar_one_or_none()

    if mapping:
        mapping.qbo_item_id = body.qbo_item_id
        mapping.qbo_item_name = body.qbo_item_name
    else:
        mapping = QBOItemMapping(
            tenant_id=user.tenant_id,
            source_type=body.source_type,
            expense_category=body.expense_category,
            qbo_item_id=body.qbo_item_id,
            qbo_item_name=body.qbo_item_name,
        )
        db.add(mapping)

    await db.commit()
    await db.refresh(mapping)
    return QBOItemMappingResponse.model_validate(mapping)


# ── Manual Sync ───────────────────────────────────────────────────────────────


@router.post("/sync/invoice/{invoice_id}")
async def qbo_sync_invoice(
    invoice_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Manually push a single invoice to QBO."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    await set_tenant_context(db, str(user.tenant_id))
    access_token = await _get_fresh_qbo_token(db, str(user.tenant_id))
    if not access_token:
        raise HTTPException(
            status_code=400, detail="QBO not connected or token expired"
        )

    qbo = await _get_qbo_integration(db, str(user.tenant_id))
    sandbox = qbo.sandbox_mode if qbo else True

    from app.services.qbo_sync import QBOSyncService

    svc = QBOSyncService(db, str(user.tenant_id), access_token, sandbox=sandbox)
    result = await svc.sync_invoice_with_retry(invoice_id)

    if result is None:
        raise HTTPException(
            status_code=502, detail="QBO sync failed — check error logs"
        )

    return {"status": "synced", "invoice_id": invoice_id}


@router.post("/sync/all")
async def qbo_sync_all(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> QBOSyncStatus:
    """Trigger a full sync of all pending invoices to QBO."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    await set_tenant_context(db, str(user.tenant_id))
    access_token = await _get_fresh_qbo_token(db, str(user.tenant_id))
    if not access_token:
        raise HTTPException(
            status_code=400, detail="QBO not connected or token expired"
        )

    qbo = await _get_qbo_integration(db, str(user.tenant_id))
    sandbox = qbo.sandbox_mode if qbo else True

    from app.services.qbo_sync import QBOSyncService
    from datetime import datetime, timezone

    started_at = datetime.now(timezone.utc)
    svc = QBOSyncService(db, str(user.tenant_id), access_token, sandbox=sandbox)
    summary = await svc.sync_all()

    completed_at = datetime.now(timezone.utc)
    synced_anything = (
        summary["invoices_synced"] > 0
        or summary["time_activities_synced"] > 0
        or summary["payments_synced"] > 0
    )
    status = (
        "success"
        if not summary["errors"]
        else ("partial" if synced_anything else "failed")
    )

    if qbo:
        qbo.last_sync_at = completed_at
        qbo.last_sync_status = status
        qbo.last_sync_error = (
            "; ".join(summary["errors"][:3]) if summary["errors"] else None
        )
        await db.commit()

    return QBOSyncStatus(
        status=status,
        invoices_synced=summary["invoices_synced"],
        time_activities_synced=summary["time_activities_synced"],
        payments_synced=summary["payments_synced"],
        errors=summary["errors"],
        started_at=started_at,
        completed_at=completed_at,
    )
