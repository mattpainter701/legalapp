"""Resolve tenant module visibility for route/nav gating."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import TenantPluginEntitlement
from app.models.tenant import TenantSettings

from app.services.plans import FULL_PLATFORM_MODULES, MODULES

FULL_PLATFORM_MODULE = "full-platform"
MODULE_ROUTES = {module_id: module.route for module_id, module in MODULES.items()}
KNOWN_MODULES = set(FULL_PLATFORM_MODULES) | {FULL_PLATFORM_MODULE}
PURCHASED_STATUSES = {"purchased", "included", "trial"}
BASIC_PORTAL_MODULES = ["plugins"]
GENERAL_MODULES = [
    "matters",
    "chat",
    "calendar",
    "tasks",
    "communications",
    "intake",
    "intake-dashboard",
    "time-tracking",
]


def _with_finance_admin(modules: list[str], user=None) -> list[str]:
    if user is not None and getattr(user, "role", None) in {"admin", "accountant"}:
        modules = [*modules, "admin"]
    return sorted(
        set(modules), key=lambda item: list(FULL_PLATFORM_MODULES).index(item)
    )


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
    # These are the core practice workspace modules. Older tenant/module
    # allowlists predate several of these ids, so keep the general workspace
    # visible instead of letting stale config amputate daily workflows.
    for module in GENERAL_MODULES:
        if module not in normalized:
            normalized.append(module)
    if "plugins" not in normalized:
        normalized.append("plugins")
    return sorted(
        set(normalized), key=lambda item: list(FULL_PLATFORM_MODULES).index(item)
    )


async def resolve_enabled_modules(
    db: AsyncSession, tenant_id: uuid.UUID, user=None
) -> tuple[list[str], str]:
    """Return enabled module ids and default route.

    Backward compatibility: tenants with no explicit module config and no known
    module entitlements receive the full platform.
    """
    if user is not None and not getattr(user, "license_active", True):
        return BASIC_PORTAL_MODULES, MODULE_ROUTES["plugins"]

    settings_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    tenant_settings = settings_result.scalar_one_or_none()
    custom_config = tenant_settings.custom_config if tenant_settings else None
    custom_config = custom_config or {}

    from app.services.plans import get_plan

    plan = get_plan(custom_config.get("plan"))
    if plan is not None:
        enabled = _with_finance_admin(list(plan.modules), user)
        default_module = (
            plan.default_module if plan.default_module in enabled else enabled[0]
        )
        return enabled, MODULE_ROUTES[default_module]

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
        enabled = (
            _expand_modules(entitled_modules)
            if entitled_modules
            else list(FULL_PLATFORM_MODULES)
        )

    if not enabled:
        enabled = ["intake-dashboard"]
    enabled = _with_finance_admin(enabled, user)

    configured_default = normalize_module_name(custom_config.get("default_module"))
    default_module = configured_default if configured_default in enabled else enabled[0]
    return enabled, MODULE_ROUTES[default_module]


async def resolve_plan_meta(
    db: AsyncSession, tenant_id: uuid.UUID
) -> tuple[str, str | None]:
    """Return (plan_id, upsell_target) for the auth payload and token claim."""
    from app.services.plans import plan_for_config

    result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    ts = result.scalar_one_or_none()
    plan = plan_for_config(ts.custom_config if ts else None)
    return plan.id, plan.upsell_target
