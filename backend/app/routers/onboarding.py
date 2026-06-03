"""Tenant onboarding wizard — guided setup after first admin login.

Steps:
  0 = not started
  1 = consent (connect MS/Google integrations)
  2 = syncing (directory users being pulled)
  3 = review (review imported users)
  4 = complete
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.user import User
from app.schemas.onboarding import (
    OnboardingStatusResponse,
    OnboardingCompleteResponse,
    IntegrationConnectionStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/onboarding", tags=["onboarding"])


async def _get_integration_status(
    db: AsyncSession, tenant_id: str
) -> dict[str, IntegrationConnectionStatus]:
    """Query TenantCredential for MS and Google connection status."""
    result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.is_active == True,
        )
    )
    creds = result.scalars().all()

    status = {}
    for provider in ("microsoft", "google"):
        match = next((c for c in creds if c.provider == provider), None)
        status[provider] = IntegrationConnectionStatus(
            connected=match is not None,
            scopes=match.scopes if match else None,
            service_account_email=match.service_account_email if match else None,
            granted_by_user_id=str(match.granted_by_user_id)
            if match and match.granted_by_user_id
            else None,
        )
    return status


@router.get("/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get current onboarding state for the tenant."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Count synced users per provider
    ms_count = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == user.tenant_id,
                User.oauth_provider == "microsoft",
            )
        )
        or 0
    )
    google_count = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == user.tenant_id,
                User.oauth_provider == "google",
            )
        )
        or 0
    )

    total = (
        await db.scalar(
            select(func.count(User.id)).where(User.tenant_id == user.tenant_id)
        )
        or 0
    )

    integrations = await _get_integration_status(db, str(user.tenant_id))

    return OnboardingStatusResponse(
        onboarding_completed=tenant.onboarding_completed,
        onboarding_step=tenant.onboarding_step,
        integrations=integrations,
        synced_users={"microsoft": ms_count, "google": google_count},
        total_users=total,
    )


@router.post("/step/{step}")
async def update_onboarding_step(
    step: int,
    request=None,
    db: AsyncSession = Depends(get_db),
):
    """Persist wizard progress."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    if step < 0 or step > 4:
        raise HTTPException(status_code=400, detail="Invalid step (0-4)")

    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.onboarding_step = step
    await db.commit()
    return {"status": "ok", "step": step}


@router.post("/complete", response_model=OnboardingCompleteResponse)
async def complete_onboarding(
    request=None,
    db: AsyncSession = Depends(get_db),
):
    """Mark onboarding as complete and initialize cloud folders."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Verify at least one integration is connected
    integrations = await _get_integration_status(db, str(user.tenant_id))
    has_any = any(s.connected for s in integrations.values())

    if not has_any:
        raise HTTPException(
            status_code=400,
            detail="At least one integration (Microsoft or Google) must be connected to complete onboarding.",
        )

    # Initialize cloud folders
    cloud_root = None
    try:
        from app.services.cloud_init import initialize_cloud_root_folder

        cloud_root = await initialize_cloud_root_folder(db, str(user.tenant_id))
        tenant.cloud_root_folder = cloud_root
    except Exception as exc:
        logger.warning("Cloud folder init failed during onboarding: %s", exc)
        # Non-fatal — admin can retry later

    tenant.onboarding_completed = True
    tenant.onboarding_step = 4
    await db.commit()

    return OnboardingCompleteResponse(status="ok", cloud_root=cloud_root)


@router.post("/skip")
async def skip_onboarding(
    request=None,
    db: AsyncSession = Depends(get_db),
):
    """Skip the integration setup — mark onboarding complete without connections."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.onboarding_completed = True
    tenant.onboarding_step = 4
    await db.commit()
    return {
        "status": "ok",
        "message": "Onboarding skipped — integrations can be set up later from Admin settings.",
    }
