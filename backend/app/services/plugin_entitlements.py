"""Shared, authoritative checks for tenant add-on entitlements."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import TenantPluginEntitlement


ACTIVE_PLUGIN_ENTITLEMENT_STATUSES = frozenset({"purchased", "included", "trial"})


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def plugin_entitlement_is_active(
    entitlement: TenantPluginEntitlement | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether an add-on can be used at this instant.

    Trials must have an explicit end date so a malformed or legacy trial cannot
    silently become perpetual. Purchased and included entitlements may be
    perpetual, but any configured start/end bounds remain authoritative.
    """
    if (
        entitlement is None
        or entitlement.status not in ACTIVE_PLUGIN_ENTITLEMENT_STATUSES
    ):
        return False
    current = now or datetime.now(timezone.utc)
    starts_at = _aware(entitlement.starts_at)
    expires_at = _aware(entitlement.expires_at)
    if starts_at is not None and starts_at > current:
        return False
    if expires_at is not None and expires_at <= current:
        return False
    return entitlement.status != "trial" or expires_at is not None


async def load_plugin_entitlement(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    plugin_name: str,
) -> TenantPluginEntitlement | None:
    return await db.scalar(
        select(TenantPluginEntitlement).where(
            TenantPluginEntitlement.tenant_id == tenant_id,
            TenantPluginEntitlement.plugin_name == plugin_name,
        )
    )


async def active_plugin_names(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> list[str]:
    rows = (
        await db.scalars(
            select(TenantPluginEntitlement)
            .where(TenantPluginEntitlement.tenant_id == tenant_id)
            .order_by(TenantPluginEntitlement.plugin_name)
        )
    ).all()
    return [
        row.plugin_name for row in rows if plugin_entitlement_is_active(row, now=now)
    ]
