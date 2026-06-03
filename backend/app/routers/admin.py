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
    UserErrorLogsResponse,
    ErrorLogResponse,
    SystemErrorLogsResponse,
    ErrorSummaryResponse,
    ErrorTrendBucket,
    ErrorResolveRequest,
    ErrorResolveResponse,
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
    force: bool = Query(
        False, description="Force deactivate even if user granted integrations"
    ),
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

    # Check if this user granted org-wide OAuth consent
    if not force:
        cred_result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.granted_by_user_id == user_id,
                TenantCredential.is_active == True,
            )
        )
        service_creds = cred_result.scalars().all()
        if service_creds:
            providers = [c.provider for c in service_creds]
            raise HTTPException(
                status_code=400,
                detail=f"This user granted org-wide OAuth consent for {', '.join(providers)}. Deactivating will break integrations. Re-authorize with another admin first, or use ?force=true.",
            )

    target_user.is_active = False
    await db.commit()


@router.get("/integrations/health")
async def integration_health(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return integration health status including who granted consent and warnings."""
    _ = await _require_admin(request, db)
    tenant_id = str(request.state.tenant_id)
    await set_tenant_context(db, tenant_id)

    cred_result = await db.execute(
        select(TenantCredential).where(TenantCredential.tenant_id == tenant_id)
    )
    creds = cred_result.scalars().all()

    providers = {}
    for provider in ("microsoft", "google"):
        match = next((c for c in creds if c.provider == provider), None)
        if not match:
            providers[provider] = {
                "connected": False,
                "service_account_email": None,
                "granted_by_user_id": None,
                "granted_by_user_email": None,
                "granted_by_user_active": None,
                "warning": None,
            }
            continue

        # Resolve grantor user info
        grantor_email = None
        grantor_active = None
        if match.granted_by_user_id:
            grantor_result = await db.execute(
                select(User).where(User.id == match.granted_by_user_id)
            )
            grantor = grantor_result.scalar_one_or_none()
            if grantor:
                grantor_email = grantor.email
                grantor_active = grantor.is_active

        warning = None
        if grantor_active is False:
            warning = f"User {grantor_email} who granted consent is deactivated — integrations may break. Re-authorize with another admin."
        elif match.token_expires_at and match.token_expires_at < datetime.now(
            timezone.utc
        ):
            warning = "OAuth token has expired. Re-connect to refresh."

        providers[provider] = {
            "connected": True,
            "service_account_email": match.service_account_email,
            "granted_by_user_id": str(match.granted_by_user_id)
            if match.granted_by_user_id
            else None,
            "granted_by_user_email": grantor_email,
            "granted_by_user_active": grantor_active,
            "granted_at": match.created_at.isoformat() if match.created_at else None,
            "expires_at": match.token_expires_at.isoformat()
            if match.token_expires_at
            else None,
            "warning": warning,
        }

    overall_health = "healthy"
    if any(p.get("warning") for p in providers.values()):
        overall_health = "attention_needed"
    if not any(p["connected"] for p in providers.values()):
        overall_health = "disconnected"

    return {"providers": providers, "overall_health": overall_health}


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
            User.is_active.is_(True),
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
            (UsageRecord.cache_hit_rag.is_(True))
            | (UsageRecord.cache_hit_llm.is_(True))
            | (UsageRecord.cache_hit_matter.is_(True)),
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
            UsageRecord.cache_hit_rag.is_(True),
            UsageRecord.created_at >= period_start,
        )
    )
    rag_hit_count = rag_hits.scalar() or 0

    llm_hits = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.cache_hit_llm.is_(True),
            UsageRecord.created_at >= period_start,
        )
    )
    llm_hit_count = llm_hits.scalar() or 0

    matter_hits = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            UsageRecord.cache_hit_matter.is_(True),
            UsageRecord.created_at >= period_start,
        )
    )
    matter_hit_count = matter_hits.scalar() or 0

    # Total cache hits
    total_hits = await db.execute(
        select(func.count(UsageRecord.id)).where(
            UsageRecord.tenant_id == admin.tenant_id,
            (UsageRecord.cache_hit_rag.is_(True))
            | (UsageRecord.cache_hit_llm.is_(True))
            | (UsageRecord.cache_hit_matter.is_(True)),
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


# ── User Billing Rate ───────────────────────────────────────────────────────


@router.patch("/users/{user_id}/billing-rate")
async def set_user_billing_rate(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    default_billing_rate: float | None = None,
):
    """Set a user's default billing rate (admin only)."""
    admin = await _require_admin(request, db)

    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == admin.tenant_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    from decimal import Decimal

    target.default_billing_rate = (
        Decimal(str(default_billing_rate)) if default_billing_rate is not None else None
    )
    await db.commit()

    return {
        "user_id": str(target.id),
        "email": target.email,
        "default_billing_rate": (
            float(target.default_billing_rate) if target.default_billing_rate else None
        ),
    }


# ── Tenant Billing Defaults ──────────────────────────────────────────────────


@router.get("/billing-defaults")
async def get_billing_defaults(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get tenant-wide billing defaults (admin only)."""
    admin = await _require_admin(request, db)

    result = await db.execute(
        select(TenantSettings).where(
            TenantSettings.tenant_id == admin.tenant_id,
        )
    )
    ts = result.scalar_one_or_none()

    defaults = {
        "default_billing_cycle": "monthly",
        "default_payment_terms": "Net 30",
        "default_tax_rate": None,
        "default_hourly_rate": None,
    }

    if ts and ts.custom_config:
        config = ts.custom_config or {}
        billing = config.get("billing", {}) or {}
        defaults.update(billing)

    return defaults


@router.patch("/billing-defaults")
async def update_billing_defaults(
    request: Request,
    db: AsyncSession = Depends(get_db),
    default_billing_cycle: str | None = None,
    default_payment_terms: str | None = None,
    default_tax_rate: float | None = None,
    default_hourly_rate: float | None = None,
):
    """Update tenant-wide billing defaults (admin only)."""
    admin = await _require_admin(request, db)

    result = await db.execute(
        select(TenantSettings).where(
            TenantSettings.tenant_id == admin.tenant_id,
        )
    )
    ts = result.scalar_one_or_none()

    if not ts:
        ts = TenantSettings(tenant_id=admin.tenant_id)
        db.add(ts)

    config = dict(ts.custom_config or {})
    billing = dict(config.get("billing", {}) or {})

    if default_billing_cycle is not None:
        billing["default_billing_cycle"] = default_billing_cycle
    if default_payment_terms is not None:
        billing["default_payment_terms"] = default_payment_terms
    if default_tax_rate is not None:
        billing["default_tax_rate"] = default_tax_rate
    if default_hourly_rate is not None:
        billing["default_hourly_rate"] = default_hourly_rate

    config["billing"] = billing
    ts.custom_config = config

    await db.commit()

    return billing


# ── Customer LLM Configuration ──────────────────────────────────────────


from pydantic import BaseModel as _PydanticBase


class CustomerLLMConfigRequest(_PydanticBase):
    use_customer_llm: bool = False
    customer_llm_provider: str | None = None  # "gemini" | "copilot"
    api_key: str | None = None
    endpoint: str | None = None
    deployment: str | None = None


@router.post("/customer-llm/configure")
async def configure_customer_llm(
    body: CustomerLLMConfigRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Configure the tenant to use their own LLM subscription (Gemini/Copilot)."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == admin.tenant_id)
    )
    ts = result.scalar_one_or_none()
    if not ts:
        ts = TenantSettings(tenant_id=admin.tenant_id)
        db.add(ts)

    ts.use_customer_llm = body.use_customer_llm
    ts.customer_llm_provider = body.customer_llm_provider

    # Store sensitive fields in JSON config
    config = {
        "endpoint": body.endpoint,
        "deployment": body.deployment,
    }
    if body.api_key:
        from app.services.token_vault import encrypt_token

        config["encrypted_api_key"] = encrypt_token(body.api_key)

    ts.customer_llm_config = config
    await db.commit()

    return {
        "status": "ok",
        "use_customer_llm": ts.use_customer_llm,
        "customer_llm_provider": ts.customer_llm_provider,
    }


@router.delete("/customer-llm/configure")
async def reset_customer_llm(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Reset to platform LLM (remove customer LLM config)."""
    admin = await _require_admin(request, db)
    await set_tenant_context(db, str(admin.tenant_id))

    result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == admin.tenant_id)
    )
    ts = result.scalar_one_or_none()
    if ts:
        ts.use_customer_llm = False
        ts.customer_llm_provider = None
        ts.customer_llm_config = None
        await db.commit()

    return {"status": "ok", "use_customer_llm": False}


# ── Permission Audit ────────────────────────────────────────────────────


SCOPES_REQUIRED_MS = [
    "offline_access",
    "User.Read.All",
    "Mail.Read",
    "Files.Read.All",
    "Sites.Read.All",
    "Calendars.ReadWrite",
]

SCOPES_REQUIRED_GOOGLE = [
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar",
]


@router.get("/permissions")
async def get_permissions_audit(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Audit granted OAuth scopes vs required scopes for each provider."""
    _ = await _require_admin(request, db)
    tenant_id = str(request.state.tenant_id)
    await set_tenant_context(db, tenant_id)

    cred_result = await db.execute(
        select(TenantCredential).where(TenantCredential.tenant_id == tenant_id)
    )
    creds = cred_result.scalars().all()

    def audit_provider(provider: str, required: list[str]) -> dict:
        match = next((c for c in creds if c.provider == provider), None)
        if not match or not match.scopes:
            return {
                "connected": False,
                "granted_scopes": [],
                "missing_required": required,
                "extra_scopes": [],
                "all_required": False,
                "health": "disconnected",
            }
        granted = [s.strip() for s in match.scopes.split(" ") if s.strip()]
        missing = [s for s in required if s not in granted]
        extra = [s for s in granted if s not in required]
        return {
            "connected": True,
            "granted_scopes": granted,
            "missing_required": missing,
            "extra_scopes": extra,
            "all_required": len(missing) == 0,
            "health": "healthy" if len(missing) == 0 else "missing_scopes",
        }

    ms_audit = audit_provider("microsoft", SCOPES_REQUIRED_MS)
    google_audit = audit_provider("google", SCOPES_REQUIRED_GOOGLE)

    overall = "healthy"
    if (
        ms_audit["health"] == "disconnected"
        and google_audit["health"] == "disconnected"
    ):
        overall = "disconnected"
    elif (
        ms_audit["health"] == "missing_scopes"
        or google_audit["health"] == "missing_scopes"
    ):
        overall = "attention_needed"

    return {
        "microsoft": ms_audit,
        "google": google_audit,
        "overall_health": overall,
    }
