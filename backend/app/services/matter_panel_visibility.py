"""Tenant presentation policy; this does not change module authorization."""

from sqlalchemy import select
from app.models.tenant import TenantSettings

MATTER_PANELS = frozenset(
    {
        "documents",
        "activity",
        "team",
        "workflow",
        "correspondence",
        "portal",
        "billing",
        "chat",
    }
)


def validate_hidden_panels(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or item not in MATTER_PANELS for item in value
    ):
        raise ValueError("Unknown matter panel")
    return sorted(set(value))


async def hidden_matter_panels(db, tenant_id):
    settings = await db.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    config = settings.custom_config if settings else None
    value = config.get("hidden_matter_panels", []) if isinstance(config, dict) else []
    try:
        return validate_hidden_panels(value)
    except ValueError:
        return []
