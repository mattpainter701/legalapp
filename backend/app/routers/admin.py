from datetime import datetime, timedelta, timezone
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conversation import UsageRecord
from app.models.error_log import ErrorLog
from app.models.tenant import Tenant, TenantSettings
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
    UserDetailResponse,
    TenantSettingsResponse,
    TenantSettingsUpdate,
    TenantDetailResponse,
    CacheAnalytics,
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
        raise HTTPException(
            status_code=400, detail="Cannot deactivate your own account"
        )

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
            func.coalesce(func.sum(UsageRecord.tokens_out), 0).label(
                "total_tokens_out"
            ),
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

    result = await db.execute(select(Tenant).where(Tenant.id == admin.tenant_id))
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

    total_result = await db.execute(select(func.count(UsageRecord.id)).where(*filters))
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
            func.coalesce(func.sum(UsageRecord.tokens_out), 0).label(
                "total_tokens_out"
            ),
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

    result = await db.execute(select(Tenant).where(Tenant.id == admin.tenant_id))
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


# ─────────────────────────────────────────────────────
# TENANT SETTINGS MANAGEMENT
# ─────────────────────────────────────────────────────


@router.get("/settings", response_model=TenantSettingsResponse)
async def get_tenant_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get tenant-level settings and configuration overrides."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == admin.tenant_id)
    )
    settings_record = result.scalar_one_or_none()

    if settings_record is None:
        # Create default settings if not exists
        settings_record = TenantSettings(
            id=__import__("uuid").uuid4(),
            tenant_id=admin.tenant_id,
        )
        db.add(settings_record)
        await db.commit()
        await db.refresh(settings_record)

    return TenantSettingsResponse.model_validate(settings_record)


@router.put("/settings", response_model=TenantSettingsResponse)
async def update_tenant_settings(
    body: TenantSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update tenant-level settings and configuration."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == admin.tenant_id)
    )
    settings_record = result.scalar_one_or_none()

    if settings_record is None:
        settings_record = TenantSettings(
            id=__import__("uuid").uuid4(),
            tenant_id=admin.tenant_id,
        )
        db.add(settings_record)

    # Update only provided fields
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if hasattr(settings_record, field):
            setattr(settings_record, field, value)

    await db.commit()
    await db.refresh(settings_record)

    return TenantSettingsResponse.model_validate(settings_record)


# ─────────────────────────────────────────────────────
# ENHANCED TENANT & USER DRILL-DOWN
# ─────────────────────────────────────────────────────


@router.get("/tenant/detailed", response_model=TenantDetailResponse)
async def get_tenant_detailed(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get enhanced tenant information with analytics and drill-down data."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    # Get tenant
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == admin.tenant_id))
    tenant = tenant_result.scalar_one_or_none()

    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Get user counts
    total_users_result = await db.execute(
        select(func.count(User.id)).where(User.tenant_id == admin.tenant_id)
    )
    total_users = total_users_result.scalar() or 0

    active_users_result = await db.execute(
        select(func.count(User.id)).where(
            User.tenant_id == admin.tenant_id,
            User.is_active == True,
        )
    )
    active_users = active_users_result.scalar() or 0

    # Get message and cost stats (30 days)
    period_start = datetime.now(timezone.utc) - timedelta(days=30)
    messages_result = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.created_at >= period_start,
        )
    )
    total_messages = messages_result.scalar() or 0

    cost_result = await db.execute(
        select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.created_at >= period_start,
        )
    )
    total_cost_usd = float(cost_result.scalar() or 0)

    # Calculate cache hit rate
    cache_hits = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.created_at >= period_start,
            (UsageRecord.cache_hit_rag == True)
            | (UsageRecord.cache_hit_llm == True)
            | (UsageRecord.cache_hit_matter == True),
        )
    )
    cache_hit_count = cache_hits.scalar() or 0
    cache_hit_rate = (
        (cache_hit_count / total_messages * 100) if total_messages > 0 else None
    )

    return TenantDetailResponse(
        id=str(tenant.id),
        name=tenant.name,
        domain=tenant.domain,
        company_name=tenant.company_name,
        staff_size=tenant.staff_size,
        address=tenant.address,
        phone=tenant.phone,
        billing_tier=tenant.billing_tier,
        flat_seat_count=tenant.flat_seat_count,
        is_active=tenant.is_active,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
        total_users=total_users,
        active_users=active_users,
        total_messages=total_messages,
        total_cost_usd=total_cost_usd,
        cache_hit_rate=cache_hit_rate,
    )


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user_detail(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get full user profile with all fields and metadata."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == admin.tenant_id,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return UserDetailResponse.model_validate(user)


@router.get("/cache-analytics", response_model=CacheAnalytics)
async def get_cache_analytics(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    """Get cache performance analytics for the tenant."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    period_start = datetime.now(timezone.utc) - timedelta(days=days)

    # Total requests
    total_result = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.created_at >= period_start,
        )
    )
    total_requests = total_result.scalar() or 0

    # Cache hits by type
    rag_hits = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.cache_hit_rag == True,
            UsageRecord.created_at >= period_start,
        )
    )
    rag_hit_count = rag_hits.scalar() or 0

    llm_hits = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.cache_hit_llm == True,
            UsageRecord.created_at >= period_start,
        )
    )
    llm_hit_count = llm_hits.scalar() or 0

    matter_hits = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.cache_hit_matter == True,
            UsageRecord.created_at >= period_start,
        )
    )
    matter_hit_count = matter_hits.scalar() or 0

    # Total cache hits
    total_hits = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            (UsageRecord.cache_hit_rag == True)
            | (UsageRecord.cache_hit_llm == True)
            | (UsageRecord.cache_hit_matter == True),
            UsageRecord.created_at >= period_start,
        )
    )
    cache_hit_count = total_hits.scalar() or 0

    # Percentages
    cache_hit_rate = (
        (cache_hit_count / total_requests * 100) if total_requests > 0 else 0
    )
    rag_hit_rate = (rag_hit_count / total_requests * 100) if total_requests > 0 else 0
    llm_hit_rate = (llm_hit_count / total_requests * 100) if total_requests > 0 else 0
    matter_hit_rate = (
        (matter_hit_count / total_requests * 100) if total_requests > 0 else 0
    )

    # Cost savings: assume cached requests save ~70% of LLM cost
    total_cost = await db.execute(
        select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.created_at >= period_start,
        )
    )
    total_cost_usd = float(total_cost.scalar() or 0)
    estimated_savings = total_cost_usd * (cache_hit_rate / 100) * 0.7

    return CacheAnalytics(
        total_requests=total_requests,
        cache_hits=cache_hit_count,
        cache_hit_rate=cache_hit_rate,
        rag_hit_rate=rag_hit_rate,
        llm_hit_rate=llm_hit_rate,
        matter_hit_rate=matter_hit_rate,
        estimated_cost_savings_usd=estimated_savings,
    )


# ─────────────────────────────────────────────────────
# ERROR LOG ENDPOINTS
# ─────────────────────────────────────────────────────


@router.get("/errors/user/{user_id}", response_model=UserErrorLogsResponse)
async def get_user_errors(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(3, ge=1, le=90),
    severity: Optional[str] = Query(None, pattern="^(critical|error|warning|info)$"),
):
    """Get recent error logs for a specific user (default: 72h rolling window)."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    filters = [
        ErrorLog.tenant_id == admin.tenant_id,
        ErrorLog.user_id == user_id,
        ErrorLog.created_at >= cutoff,
    ]
    if severity:
        filters.append(ErrorLog.severity == severity)

    total_result = await db.execute(select(func.count(ErrorLog.id)).where(*filters))
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(ErrorLog).where(*filters).order_by(ErrorLog.created_at.desc()).limit(500)
    )
    errors = rows_result.scalars().all()

    return UserErrorLogsResponse(
        errors=[ErrorLogResponse.model_validate(e) for e in errors],
        total=total,
        days=days,
    )


@router.get("/errors/system", response_model=SystemErrorLogsResponse)
async def get_system_errors(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90),
    severity: Optional[str] = Query(None, pattern="^(critical|error|warning|info)$"),
    error_type: Optional[str] = Query(None),
):
    """Get system-level error logs with optional filtering."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    filters = [
        ErrorLog.tenant_id == admin.tenant_id,
        ErrorLog.created_at >= cutoff,
    ]
    if severity:
        filters.append(ErrorLog.severity == severity)
    if error_type:
        filters.append(ErrorLog.error_type == error_type)

    total_result = await db.execute(select(func.count(ErrorLog.id)).where(*filters))
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(ErrorLog).where(*filters).order_by(ErrorLog.created_at.desc()).limit(500)
    )
    errors = rows_result.scalars().all()

    return SystemErrorLogsResponse(
        errors=[ErrorLogResponse.model_validate(e) for e in errors],
        total=total,
        days=days,
        severity=severity,
    )


@router.get("/errors/summary", response_model=ErrorSummaryResponse)
async def get_error_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    """Get error summary with counts by severity/type and daily trend data."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Total count
    total_result = await db.execute(
        select(func.count(ErrorLog.id)).where(
            ErrorLog.tenant_id == admin.tenant_id,
            ErrorLog.created_at >= cutoff,
        )
    )
    total_errors = total_result.scalar_one()

    # By severity
    sev_result = await db.execute(
        select(ErrorLog.severity, func.count(ErrorLog.id))
        .where(
            ErrorLog.tenant_id == admin.tenant_id,
            ErrorLog.created_at >= cutoff,
        )
        .group_by(ErrorLog.severity)
    )
    by_severity = {row.severity: row.count for row in sev_result.all()}

    # By error type
    type_result = await db.execute(
        select(ErrorLog.error_type, func.count(ErrorLog.id))
        .where(
            ErrorLog.tenant_id == admin.tenant_id,
            ErrorLog.created_at >= cutoff,
        )
        .group_by(ErrorLog.error_type)
    )
    by_type = {row.error_type: row.count for row in type_result.all()}

    # Daily trend buckets
    trend_result = await db.execute(
        select(
            func.date(ErrorLog.created_at).label("day"),
            ErrorLog.severity,
            func.count(ErrorLog.id).label("cnt"),
        )
        .where(
            ErrorLog.tenant_id == admin.tenant_id,
            ErrorLog.created_at >= cutoff,
        )
        .group_by(func.date(ErrorLog.created_at), ErrorLog.severity)
        .order_by(func.date(ErrorLog.created_at))
    )
    # Build buckets
    trend_map: dict = {}
    for row in trend_result.all():
        day_str = str(row.day)
        if day_str not in trend_map:
            trend_map[day_str] = {"critical": 0, "error": 0, "warning": 0, "info": 0}
        trend_map[day_str][row.severity] = row.cnt

    trend = [
        ErrorTrendBucket(
            date=day,
            total=sum(counts.values()),
            critical=counts["critical"],
            error=counts["error"],
            warning=counts["warning"],
            info=counts["info"],
        )
        for day, counts in sorted(trend_map.items())
    ]

    return ErrorSummaryResponse(
        total_errors=total_errors,
        by_severity=by_severity,
        by_type=by_type,
        trend=trend,
        days=days,
    )


@router.patch("/errors/{error_id}/resolve", response_model=ErrorResolveResponse)
async def resolve_error(
    error_id: str,
    body: ErrorResolveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mark an error log entry as resolved with optional notes."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    result = await db.execute(
        select(ErrorLog).where(
            ErrorLog.id == error_id,
            ErrorLog.tenant_id == admin.tenant_id,
        )
    )
    error_log = result.scalar_one_or_none()
    if not error_log:
        raise HTTPException(status_code=404, detail="Error log entry not found")

    error_log.is_resolved = True
    error_log.resolved_at = datetime.now(timezone.utc)
    if body.resolution_notes:
        error_log.resolution_notes = body.resolution_notes
    await db.commit()
    await db.refresh(error_log)

    return ErrorResolveResponse(
        id=str(error_log.id),
        is_resolved=error_log.is_resolved,
        resolved_at=error_log.resolved_at,
        resolution_notes=error_log.resolution_notes,
    )
