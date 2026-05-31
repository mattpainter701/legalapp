from datetime import datetime, timedelta, timezone
from typing import List

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conversation import UsageRecord
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.admin import (
    BillingUpdate,
    TenantInfo,
    UsageStats,
    UserList,
    UserResponse,
)

settings = get_settings()
router = APIRouter(prefix="/admin", tags=["admin"])


async def _require_admin(request: Request, db: AsyncSession) -> User:
    """Shared admin gate: get current user and verify admin role."""
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/users", response_model=UserList)
async def list_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all users in the current tenant."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    result = await db.execute(
        select(User)
        .where(User.tenant_id == admin.tenant_id)
        .order_by(User.created_at.asc())
    )
    users = result.scalars().all()

    return UserList(
        users=[
            UserResponse(
                id=str(u.id),
                email=u.email,
                full_name=u.full_name,
                role=u.role,
                is_active=u.is_active,
                created_at=u.created_at,
            )
            for u in users
        ],
        total=len(users),
    )


@router.delete("/users/{user_id}", status_code=204)
async def deactivate_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a user (set is_active=False). Cannot deactivate yourself."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    if str(admin.id) == user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == admin.tenant_id,
        )
    )
    target_user = result.scalar_one_or_none()

    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.is_active = False
    await db.commit()


@router.get("/usage", response_model=UsageStats)
async def get_usage_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get usage statistics for the last 30 days."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=30)

    result = await db.execute(
        select(
            func.coalesce(func.sum(UsageRecord.tokens_in), 0).label("total_tokens_in"),
            func.coalesce(func.sum(UsageRecord.tokens_out), 0).label("total_tokens_out"),
            func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("total_cost_usd"),
            func.count(UsageRecord.id).label("request_count"),
        ).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.created_at >= period_start,
            UsageRecord.created_at <= period_end,
        )
    )
    row = result.one()

    return UsageStats(
        total_tokens_in=int(row.total_tokens_in),
        total_tokens_out=int(row.total_tokens_out),
        total_cost_usd=float(row.total_cost_usd),
        request_count=int(row.request_count),
        period_start=period_start,
        period_end=period_end,
    )


@router.get("/tenant", response_model=TenantInfo)
async def get_tenant_info(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get current tenant information."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    result = await db.execute(
        select(Tenant).where(Tenant.id == admin.tenant_id)
    )
    tenant = result.scalar_one_or_none()

    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return TenantInfo(
        id=str(tenant.id),
        name=tenant.name,
        domain=tenant.domain,
        billing_tier=tenant.billing_tier,
        flat_seat_count=tenant.flat_seat_count,
        is_active=tenant.is_active,
        created_at=tenant.created_at,
    )


@router.put("/billing", response_model=TenantInfo)
async def update_billing(
    body: BillingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update billing tier or seat count; sync with Stripe if configured."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    if body.billing_tier not in ("flat", "payg"):
        raise HTTPException(
            status_code=400, detail="billing_tier must be 'flat' or 'payg'"
        )

    result = await db.execute(
        select(Tenant).where(Tenant.id == admin.tenant_id)
    )
    tenant = result.scalar_one_or_none()

    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.billing_tier = body.billing_tier

    if body.seat_count is not None:
        if body.seat_count < 0:
            raise HTTPException(status_code=400, detail="seat_count must be >= 0")
        tenant.flat_seat_count = body.seat_count

    # Sync with Stripe if customer exists and Stripe is configured
    if tenant.stripe_customer_id and settings.STRIPE_SECRET_KEY:
        try:
            stripe.api_key = settings.STRIPE_SECRET_KEY
            stripe.Customer.modify(
                tenant.stripe_customer_id,
                metadata={
                    "billing_tier": body.billing_tier,
                    "flat_seat_count": str(tenant.flat_seat_count),
                },
            )
        except stripe.StripeError:
            # Non-fatal: log but don't block the update
            pass

    await db.commit()
    await db.refresh(tenant)

    return TenantInfo(
        id=str(tenant.id),
        name=tenant.name,
        domain=tenant.domain,
        billing_tier=tenant.billing_tier,
        flat_seat_count=tenant.flat_seat_count,
        is_active=tenant.is_active,
        created_at=tenant.created_at,
    )
