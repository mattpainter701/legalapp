"""Shared pre-spend token budget enforcement."""

from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import UsageRecord
from app.models.tenant import TenantSettings


async def check_token_budget(db: AsyncSession, user) -> None:
    """Raise HTTP 429 before model spend when the daily budget is exhausted."""

    settings_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
    )
    tenant_settings = settings_result.scalar_one_or_none()
    if not tenant_settings or not tenant_settings.max_daily_tokens:
        return

    today_start = datetime.combine(date.today(), datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    token_result = await db.execute(
        select(
            func.coalesce(func.sum(UsageRecord.tokens_in + UsageRecord.tokens_out), 0)
        ).where(
            UsageRecord.tenant_id == user.tenant_id,
            UsageRecord.created_at >= today_start,
        )
    )
    tokens_today = token_result.scalar() or 0
    if tokens_today >= tenant_settings.max_daily_tokens:
        raise HTTPException(
            status_code=429,
            detail="Daily token limit reached. Contact your administrator.",
        )
