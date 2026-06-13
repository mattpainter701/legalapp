"""Gate Teams features to tenants with an active Microsoft integration.

A tenant is "Teams-enabled" iff it has an active ``TenantCredential``
(provider="microsoft") whose granted scopes include every Teams scope. This
mirrors the scope-checking pattern in ``app.routers.integrations`` but is kept
self-contained so Teams routers/services can import it without coupling.
"""

import logging

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.tenant_credential import TenantCredential
from app.services.teams import TEAMS_REQUIRED_SCOPES

settings = get_settings()
logger = logging.getLogger(__name__)


def missing_teams_scopes(granted: str | None) -> list[str]:
    """Return the Teams scopes not present in the granted scope string."""
    granted_set = set((granted or "").split())
    return sorted(s for s in TEAMS_REQUIRED_SCOPES.split() if s not in granted_set)


async def get_teams_status(db: AsyncSession, tenant_id: str) -> tuple[bool, list[str]]:
    """Return ``(teams_connected, missing_scopes)`` for a tenant.

    ``teams_connected`` is True only when the master feature flag is on, an
    active Microsoft credential exists, and no Teams scopes are missing.
    """
    if not settings.TEAMS_FEATURE_ENABLED:
        return False, list(TEAMS_REQUIRED_SCOPES.split())

    await set_tenant_context(db, str(tenant_id))
    result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == "microsoft",
            TenantCredential.is_active,
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        return False, list(TEAMS_REQUIRED_SCOPES.split())

    missing = missing_teams_scopes(cred.scopes)
    return (len(missing) == 0), missing


async def require_teams_enabled(request: Request, db: AsyncSession):
    """FastAPI-style dependency: ensure the caller's tenant is Teams-enabled.

    Returns ``(user, tenant_id)``. Raises:
    - 403 ``teams_feature_disabled`` when the master flag is off
    - 409 ``teams_not_connected`` when there is no active Microsoft credential
    - 403 ``teams_scopes_missing`` (body carries ``missing_scopes``) otherwise
    """
    user = await get_current_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if not settings.TEAMS_FEATURE_ENABLED:
        raise HTTPException(status_code=403, detail="teams_feature_disabled")

    tenant_id = str(user.tenant_id)
    await set_tenant_context(db, tenant_id)
    result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == "microsoft",
            TenantCredential.is_active,
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=409, detail="teams_not_connected")

    missing = missing_teams_scopes(cred.scopes)
    if missing:
        raise HTTPException(
            status_code=403,
            detail={"error": "teams_scopes_missing", "missing_scopes": missing},
        )

    return user, tenant_id
