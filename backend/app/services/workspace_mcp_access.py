"""Tenant and user policy helpers for Workspace MCP access."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import TenantSettings


async def tenant_workspace_mcp_default(
    db: AsyncSession, tenant_id: uuid.UUID | str
) -> bool:
    """Return the administrator-selected default for newly provisioned users."""

    resolved_tenant_id = (
        tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    )
    value = await db.scalar(
        select(TenantSettings.default_workspace_mcp_enabled).where(
            TenantSettings.tenant_id == resolved_tenant_id
        )
    )
    # Existing tenants without a settings row preserve the historical behavior:
    # eligible users may connect after explicit OAuth consent.
    return True if value is None else bool(value)
