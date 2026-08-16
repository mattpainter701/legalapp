"""Shared fail-closed checks for tenant account state."""

from datetime import datetime, timezone

from typing import TypeVar

from fastapi import HTTPException


TenantT = TypeVar("TenantT")


def require_active_tenant(tenant: TenantT | None) -> TenantT:
    """Return an active tenant or reject normal application access.

    Platform-operator authentication does not use this helper.  It must remain
    able to inspect and reactivate suspended tenants, while every tenant user
    session fails closed as soon as ``Tenant.is_active`` is cleared.
    """

    if tenant is None or not bool(getattr(tenant, "is_active", False)):
        raise HTTPException(status_code=403, detail="Tenant account is inactive")
    expires_at = getattr(tenant, "expires_at", None)
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=403, detail="Tenant access has expired")
    return tenant
