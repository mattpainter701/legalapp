from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin
from app.schemas.user_sync import UserSyncResponse, UserSyncResult
from app.services.user_sync import user_sync

router = APIRouter(prefix="/api/sync/users", tags=["user-sync"])


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
