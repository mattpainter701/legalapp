import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin
from app.models.tenant import Tenant
from app.schemas.user_sync import UserSyncResponse, UserSyncResult
from app.services.user_sync import user_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sync/users", tags=["user-sync"])


async def _advance_onboarding_step(db: AsyncSession, tenant_id: str) -> None:
    """If tenant is in onboarding step 2 (syncing), advance to step 3 (review)."""
    try:
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant and not tenant.onboarding_completed and tenant.onboarding_step == 2:
            tenant.onboarding_step = 3
            await db.commit()
    except Exception as exc:
        logger.warning("Failed to advance onboarding step: %s", exc)


@router.post("/microsoft", response_model=UserSyncResult)
async def sync_microsoft_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)

    result = await user_sync.sync_microsoft_users(db, tenant_id)
    await _advance_onboarding_step(db, tenant_id)
    return UserSyncResult(**result)


@router.post("/google", response_model=UserSyncResult)
async def sync_google_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)

    result = await user_sync.sync_google_users(db, tenant_id)
    await _advance_onboarding_step(db, tenant_id)
    return UserSyncResult(**result)


@router.post("/all", response_model=UserSyncResponse)
async def sync_all_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)

    result = await user_sync.sync_all(db, tenant_id)

    ms_result = None
    google_result = None

    if result.get("microsoft"):
        ms_result = UserSyncResult(**result["microsoft"])
    if result.get("google"):
        google_result = UserSyncResult(**result["google"])

    return UserSyncResponse(microsoft=ms_result, google=google_result)
