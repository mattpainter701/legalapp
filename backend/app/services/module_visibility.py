"""Resolve tenant module visibility for route/nav gating."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import TenantPluginEntitlement
from app.models.tenant import TenantSettings

FULL_PLATFORM_MODULE = "full-platform"

MODULE_ROUTES = {
    "matters": "/matters",
    "chat": "/chat",
    "calendar": "/calendar",
    "communications": "/communications",
    "contacts": "/contacts",
    "intake": "/intake",
    "intake-dashboard": "/intake/dashboard",
    "templates": "/templates",
    "time-tracking": "/time-tracking",
    "invoices": "/invoices",
    "billing": "/billing",
    "trust": "/trust",
    "reports": "/reports",
    "plugins": "/plugins",
    "admin": "/admin",
    "mcp": "/mcp",
    "onboarding": "/onboarding",
}

FULL_PLATFORM_MODULES = tuple(MODULE_ROUTES.keys())
KNOWN_MODULES = set(FULL_PLATFORM_MODULES) | {FULL_PLATFORM_MODULE}
PURCHASED_STATUSES = {"purchased", "included", "trial"}


def normalize_module_name(value: str | None) -> str | None:
    if not value:
        return None
    module = value.strip().lower().replace("_", "-")
    return module if module in KNOWN_MODULES else None


def _expand_modules(modules: list[str]) -> list[str]:
    normalized = []
    for value in modules:
        module = normalize_module_name(value)
        if module:
            normalized.append(module)
    if FULL_PLATFORM_MODULE in normalized:
        return list(FULL_PLATFORM_MODULES)
    return sorted(set(normalized), key=lambda item: list(FULL_PLATFORM_MODULES).index(item))


async def resolve_enabled_modules(
    db: AsyncSession, tenant_id: uuid.UUID
) -> tuple[list[str], str]:
    """Return enabled module ids and default route.

    Backward compatibility: tenants with no explicit module config and no known
    module entitlements receive the full platform.
    """
    settings_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    tenant_settings = settings_result.scalar_one_or_none()
    custom_config = tenant_settings.custom_config if tenant_settings else None
    custom_config = custom_config or {}

    configured_modules = custom_config.get("enabled_modules")
    if isinstance(configured_modules, list):
        enabled = _expand_modules(configured_modules)
    else:
        entitlement_result = await db.execute(
            select(TenantPluginEntitlement).where(
                TenantPluginEntitlement.tenant_id == tenant_id,
                TenantPluginEntitlement.status.in_(PURCHASED_STATUSES),
            )
        )
        entitled_modules = [
            row.plugin_name
            for row in entitlement_result.scalars().all()
            if normalize_module_name(row.plugin_name)
        ]
        enabled = _expand_modules(entitled_modules) if entitled_modules else list(FULL_PLATFORM_MODULES)

    if not enabled:
        enabled = ["intake-dashboard"]

    configured_default = normalize_module_name(custom_config.get("default_module"))
    default_module = configured_default if configured_default in enabled else enabled[0]
    return enabled, MODULE_ROUTES[default_module]
