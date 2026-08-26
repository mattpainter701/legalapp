"""Operator controls and observability for global Assistant infrastructure."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.platform import PlatformSetting
from app.services.background_ai_quota import (
    BACKGROUND_ROUTE_CONFIG_KEY,
    background_quota_snapshot,
)
from app.services.operator_audit import record_operator_audit
from app.services.platform_auth import require_platform_token


router = APIRouter(prefix="/platform/assistant", tags=["platform-assistant"])


class BackgroundQuotaUpdate(BaseModel):
    account_five_hour: int = Field(ge=1, le=10_000_000)
    account_weekly: int = Field(ge=1, le=100_000_000)
    account_monthly: int = Field(ge=1, le=100_000_000)
    tenant_five_hour: int = Field(ge=1, le=10_000_000)
    tenant_weekly: int = Field(ge=1, le=100_000_000)
    tenant_monthly: int = Field(ge=1, le=100_000_000)
    reservation_ttl_minutes: int = Field(default=15, ge=1, le=1440)


def _require_platform_key(request: Request) -> None:
    require_platform_token(request)


@router.get("/background-usage")
async def get_background_usage(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)
    return await background_quota_snapshot(db)


@router.put("/background-quota")
async def update_background_quota(
    body: BackgroundQuotaUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)
    row = await db.scalar(
        select(PlatformSetting).where(
            PlatformSetting.key == BACKGROUND_ROUTE_CONFIG_KEY
        )
    )
    value = dict(row.value or {}) if row else {}
    previous = dict(value.get("quota") or {})
    value["quota"] = body.model_dump()
    if row is None:
        row = PlatformSetting(key=BACKGROUND_ROUTE_CONFIG_KEY, value=value)
        db.add(row)
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    await record_operator_audit(
        db,
        request,
        action="assistant.background_quota_updated",
        resource_type="background_ai_pool",
        resource_id="background-default",
        metadata={"from": previous, "to": body.model_dump()},
    )
    await db.commit()
    return await background_quota_snapshot(db)
