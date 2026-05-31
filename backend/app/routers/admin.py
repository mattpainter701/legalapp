from datetime import datetime, timedelta, timezone
from typing import List, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conversation import UsageRecord
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.admin import (
    AuditLog,
    AuditRecord,
    BillingUpdate,
    TenantInfo,
    UsageStats,
    UserList,
    UserResponse,
    UserUsageBreakdown,
    UserUsageRow,
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


@router.get("/audit", response_model=AuditLog)
async def get_audit_log(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    user_id: Optional[str] = Query(None),
    operation_type: Optional[str] = Query(None),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
):
    """Paginated audit log of all LLM requests for this tenant."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    filters = [UsageRecord.tenant_id == admin.tenant_id]
    if user_id:
        filters.append(UsageRecord.user_id == user_id)
    if operation_type:
        filters.append(UsageRecord.operation_type == operation_type)
    if start:
        filters.append(UsageRecord.created_at >= start)
    if end:
        filters.append(UsageRecord.created_at <= end)

    total_result = await db.execute(
        select(func.count(UsageRecord.id)).where(*filters)
    )
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(UsageRecord, User.email.label("user_email"))
        .join(User, User.id == UsageRecord.user_id, isouter=True)
        .where(*filters)
        .order_by(UsageRecord.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = rows_result.all()

    records = [
        AuditRecord(
            id=str(rec.id),
            user_id=str(rec.user_id),
            user_email=email,
            operation_type=rec.operation_type,
            model_used=rec.model_used,
            tokens_in=rec.tokens_in,
            tokens_out=rec.tokens_out,
            cost_usd=float(rec.cost_usd) if rec.cost_usd is not None else None,
            query_text=rec.query_text,
            rag_chunks_retrieved=rec.rag_chunks_retrieved,
            rag_source_ids=rec.rag_source_ids,
            ip_address=rec.ip_address,
            user_agent=rec.user_agent,
            created_at=rec.created_at,
        )
        for rec, email in rows
    ]

    return AuditLog(records=records, total=total, page=page, limit=limit)


@router.get("/usage/by-user", response_model=UserUsageBreakdown)
async def get_usage_by_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    """Per-user token and cost breakdown for the given window (default 30 days)."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=days)

    rows_result = await db.execute(
        select(
            UsageRecord.user_id,
            User.email.label("user_email"),
            func.count(UsageRecord.id).label("request_count"),
            func.coalesce(func.sum(UsageRecord.tokens_in), 0).label("total_tokens_in"),
            func.coalesce(func.sum(UsageRecord.tokens_out), 0).label("total_tokens_out"),
            func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("total_cost_usd"),
        )
        .join(User, User.id == UsageRecord.user_id, isouter=True)
        .where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.created_at >= period_start,
            UsageRecord.created_at <= period_end,
        )
        .group_by(UsageRecord.user_id, User.email)
        .order_by(func.sum(UsageRecord.tokens_in + UsageRecord.tokens_out).desc())
    )
    rows = rows_result.all()

    return UserUsageBreakdown(
        users=[
            UserUsageRow(
                user_id=str(row.user_id),
                user_email=row.user_email or "unknown",
                request_count=int(row.request_count),
                total_tokens_in=int(row.total_tokens_in),
                total_tokens_out=int(row.total_tokens_out),
                total_cost_usd=float(row.total_cost_usd),
            )
            for row in rows
        ],
        period_start=period_start,
        period_end=period_end,
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
