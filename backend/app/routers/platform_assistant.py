"""Operator controls and observability for global Assistant infrastructure."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.platform import PlatformSetting
from app.services.ai_price_card import MICROS_PER_USD
from app.services.background_ai_quota import (
    BACKGROUND_ROUTE_CONFIG_KEY,
    background_quota_snapshot,
)
from app.services.operator_audit import record_operator_audit
from app.services.platform_auth import require_platform_token


router = APIRouter(prefix="/platform/assistant", tags=["platform-assistant"])

_DEFAULT_SPEND_USD = {
    "account_five_hour": Decimal("12"),
    "account_weekly": Decimal("30"),
    "account_monthly": Decimal("60"),
    "tenant_five_hour": Decimal("3"),
    "tenant_weekly": Decimal("8"),
    "tenant_monthly": Decimal("15"),
}


class BackgroundQuotaUpdate(BaseModel):
    """Operators set the pool budget in dollars; requests stay a backstop.

    Dollar windows are the provider's real limit, so they are what admission
    enforces. The request ceilings remain settable as a coarse second guard.
    """

    account_five_hour: int = Field(ge=1, le=10_000_000)
    account_weekly: int = Field(ge=1, le=100_000_000)
    account_monthly: int = Field(ge=1, le=100_000_000)
    tenant_five_hour: int = Field(ge=1, le=10_000_000)
    tenant_weekly: int = Field(ge=1, le=100_000_000)
    tenant_monthly: int = Field(ge=1, le=100_000_000)
    reservation_ttl_minutes: int = Field(default=15, ge=1, le=1440)
    # Optional preserves compatibility with older operator clients that only
    # know the request ceilings. Omitted dollar windows retain their current
    # values instead of resetting a carefully tuned spend budget.
    account_five_hour_usd: Decimal | None = Field(
        default=None, ge=Decimal("0.01"), le=100_000
    )
    account_weekly_usd: Decimal | None = Field(
        default=None, ge=Decimal("0.01"), le=1_000_000
    )
    account_monthly_usd: Decimal | None = Field(
        default=None, ge=Decimal("0.01"), le=1_000_000
    )
    tenant_five_hour_usd: Decimal | None = Field(
        default=None, ge=Decimal("0.01"), le=100_000
    )
    tenant_weekly_usd: Decimal | None = Field(
        default=None, ge=Decimal("0.01"), le=1_000_000
    )
    tenant_monthly_usd: Decimal | None = Field(
        default=None, ge=Decimal("0.01"), le=1_000_000
    )

    def to_stored_quota(self, previous: dict | None = None) -> dict[str, int]:
        """Persist money as integer micros; dollars never round-trip as floats."""

        stored = {
            "account_five_hour": self.account_five_hour,
            "account_weekly": self.account_weekly,
            "account_monthly": self.account_monthly,
            "tenant_five_hour": self.tenant_five_hour,
            "tenant_weekly": self.tenant_weekly,
            "tenant_monthly": self.tenant_monthly,
            "reservation_ttl_minutes": self.reservation_ttl_minutes,
        }
        prior = previous or {}
        for window, default_usd in _DEFAULT_SPEND_USD.items():
            dollars = getattr(self, f"{window}_usd")
            if dollars is None:
                existing = prior.get(f"{window}_micros")
                try:
                    existing_micros = int(existing)
                except (TypeError, ValueError):
                    existing_micros = 0
                if existing_micros > 0:
                    stored[f"{window}_micros"] = existing_micros
                    continue
                dollars = default_usd
            stored[f"{window}_micros"] = int(
                (dollars * MICROS_PER_USD).to_integral_value(rounding=ROUND_HALF_UP)
            )
        return stored


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
    stored_quota = body.to_stored_quota(previous)
    value["quota"] = stored_quota
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
        metadata={"from": previous, "to": stored_quota},
    )
    await db.commit()
    return await background_quota_snapshot(db)
