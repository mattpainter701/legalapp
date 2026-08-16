"""Fail-closed access policy for the Microsoft Office pilot."""

from uuid import UUID

from fastapi import HTTPException

from app.config import get_settings


settings = get_settings()


def require_office_globally_enabled() -> None:
    """Hide the Office surface until the deployment-level switch is enabled."""

    if not settings.OFFICE_ASSISTANT_ENABLED:
        raise HTTPException(status_code=404, detail="Office assistant is not enabled")


def require_office_pilot_tenant(tenant_id: UUID) -> None:
    """Allow only explicitly listed LawHand tenants into the Office pilot.

    An empty or malformed allowlist denies every tenant. This makes turning on
    the global deployment flag safe before a pilot tenant has been selected.
    """

    require_office_globally_enabled()
    configured = [
        value.strip()
        for value in settings.OFFICE_ASSISTANT_PILOT_TENANT_IDS.split(",")
        if value.strip()
    ]
    try:
        allowed_tenants = {UUID(value) for value in configured}
    except ValueError:
        allowed_tenants = set()

    if tenant_id not in allowed_tenants:
        raise HTTPException(status_code=404, detail="Office assistant is not enabled")


def require_office_for_user(user) -> None:
    """Apply the pilot allowlist, with an explicit synthetic-demo exception."""
    require_office_globally_enabled()
    tenant = getattr(user, "tenant", None)
    if tenant is not None and tenant.billing_tier == "demo":
        return
    require_office_pilot_tenant(user.tenant_id)
