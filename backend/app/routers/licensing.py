"""License and seat management for tenant billing."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import require_admin
from app.models.tenant import Tenant
from app.models.user import User
from app.models.conversation import UsageRecord

router = APIRouter(prefix="/api/admin", tags=["licensing"])


class UserLicenseRow(BaseModel):
    user_id: str
    email: str
    full_name: str | None = None
    license_active: bool
    role: str
    tokens_used: int = 0
    cost_usd: float = 0.0

    class Config:
        from_attributes = True


class LicensingResponse(BaseModel):
    billing_tier: str
    flat_seat_count: int
    total_users: int
    active_users: int
    licensed_users: int
    available_seats: int | None = None  # None for PAYG
    approaching_limit: bool = False
    users: list[UserLicenseRow] = []


class ToggleLicenseRequest(BaseModel):
    license_active: bool


class UpdateSeatsRequest(BaseModel):
    flat_seat_count: int


@router.get("/licensing", response_model=LicensingResponse)
async def get_licensing_info(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get licensing overview: seat counts, per-user license status, and usage."""
    await require_admin(request, db)
    tenant_id = request.state.tenant_id
    await set_tenant_context(db, tenant_id)

    # Tenant
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # User counts
    total = (
        await db.scalar(select(func.count(User.id)).where(User.tenant_id == tenant_id))
        or 0
    )
    active = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id, User.is_active.is_(True)
            )
        )
        or 0
    )
    licensed = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id, User.license_active.is_(True)
            )
        )
        or 0
    )

    # Per-user usage (last 30 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    usage_rows = await db.execute(
        select(
            UsageRecord.user_id,
            func.coalesce(
                func.sum(UsageRecord.tokens_in + UsageRecord.tokens_out), 0
            ).label("total_tokens"),
            func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("total_cost"),
        )
        .where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.created_at >= cutoff,
        )
        .group_by(UsageRecord.user_id)
    )
    usage_by_user = {
        str(row.user_id): (row.total_tokens, float(row.total_cost))
        for row in usage_rows
    }

    # Users list
    users_result = await db.execute(
        select(User).where(User.tenant_id == tenant_id).order_by(User.email)
    )
    users = users_result.scalars().all()

    user_rows = []
    for u in users:
        tokens, cost = usage_by_user.get(str(u.id), (0, 0.0))
        user_rows.append(
            UserLicenseRow(
                user_id=str(u.id),
                email=u.email,
                full_name=u.full_name,
                license_active=u.license_active,
                role=u.role,
                tokens_used=tokens,
                cost_usd=cost,
            )
        )

    available = None
    approaching = False
    if tenant.billing_tier == "flat":
        available = max(0, tenant.flat_seat_count - licensed)
        approaching = (
            tenant.flat_seat_count > 0 and licensed >= tenant.flat_seat_count * 0.9
        )

    return LicensingResponse(
        billing_tier=tenant.billing_tier,
        flat_seat_count=tenant.flat_seat_count,
        total_users=total,
        active_users=active,
        licensed_users=licensed,
        available_seats=available,
        approaching_limit=approaching,
        users=user_rows,
    )


@router.put("/users/{user_id}/license")
async def toggle_user_license(
    user_id: str,
    body: ToggleLicenseRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Toggle whether a user consumes a license seat."""
    await require_admin(request, db)
    tenant_id = request.state.tenant_id
    await set_tenant_context(db, tenant_id)

    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # If deactivating license, check if they granted integrations
    if not body.license_active:
        from app.models.tenant_credential import TenantCredential

        cred_result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.granted_by_user_id == user.id,
                TenantCredential.is_active.is_(True),
            )
        )
        if cred_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="This user granted org-wide OAuth consent. Deactivating their license may break integrations. Re-authorize with another admin first.",
            )

    user.license_active = body.license_active
    await db.commit()
    return {
        "status": "ok",
        "user_id": str(user.id),
        "license_active": user.license_active,
    }


@router.put("/licensing/seats")
async def update_seat_count(
    body: UpdateSeatsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update flat_seat_count for the tenant."""
    await require_admin(request, db)
    tenant_id = request.state.tenant_id
    await set_tenant_context(db, tenant_id)

    if body.flat_seat_count < 0:
        raise HTTPException(status_code=400, detail="Seat count cannot be negative")

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if tenant.billing_tier != "flat":
        raise HTTPException(
            status_code=400, detail="Seat count is only configurable on the Flat plan"
        )

    # Count currently licensed users
    licensed = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id, User.license_active.is_(True)
            )
        )
        or 0
    )

    warning = None
    if body.flat_seat_count < licensed:
        warning = f"New seat count ({body.flat_seat_count}) is below currently licensed users ({licensed}). Over-limit users will not be auto-deactivated."

    tenant.flat_seat_count = body.flat_seat_count
    await db.commit()

    return {
        "status": "ok",
        "flat_seat_count": tenant.flat_seat_count,
        "licensed_users": licensed,
        "warning": warning,
    }
