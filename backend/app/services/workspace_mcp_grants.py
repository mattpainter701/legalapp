"""Fail-closed validation for persisted workspace-MCP consent grants."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace_mcp_grant import WorkspaceMCPGrant


class WorkspaceMCPGrantError(ValueError):
    """The access token is not backed by a matching active consent grant."""


async def require_active_workspace_grant(
    db: AsyncSession,
    *,
    grant_id: str,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    client_id: str,
    token_scopes: frozenset[str],
    now: datetime | None = None,
) -> WorkspaceMCPGrant:
    """Bind JWT claims to one live database grant under explicit predicates."""

    try:
        grant_uuid = uuid.UUID(grant_id)
    except (TypeError, ValueError) as exc:
        raise WorkspaceMCPGrantError("Workspace consent grant is invalid") from exc

    grant = await db.scalar(
        select(WorkspaceMCPGrant).where(
            WorkspaceMCPGrant.id == grant_uuid,
            WorkspaceMCPGrant.tenant_id == tenant_id,
            WorkspaceMCPGrant.user_id == user_id,
            WorkspaceMCPGrant.client_id == client_id,
        )
    )
    moment = now or datetime.now(timezone.utc)
    if (
        grant is None
        or grant.tenant_id != tenant_id
        or grant.user_id != user_id
        or grant.client_id != client_id
        or not grant.is_active(moment)
    ):
        raise WorkspaceMCPGrantError("Workspace consent grant is unavailable")
    if not token_scopes or token_scopes - grant.scope_set:
        raise WorkspaceMCPGrantError("Workspace access token exceeds its consent grant")
    grant.last_used_at = moment
    return grant
