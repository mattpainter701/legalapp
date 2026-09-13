"""Revoking a user's Workspace MCP grants, and prompting them to reconnect.

Two things end a user's session-bearing credentials for security reasons:
turning on Privacy Mode, and resetting a password. Both must also disconnect
that user's Workspace MCP assistants, because an MCP access token authenticates
with its own audience-bound credential and would otherwise outlive the session
it was consented from — leaving a connected assistant reachable by whoever
prompted the reset.

Reconnecting cannot be driven from here. An MCP client starts its own OAuth
flow, so the most the product can do is be unambiguous about *which* assistants
dropped and *why*, which is what :func:`pending_reconnect_clients` feeds.

Research MCP is deliberately untouched: it reaches only public authority and is
a separate product with its own connection controls.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace_mcp_grant import WorkspaceMCPGrant

logger = logging.getLogger(__name__)

#: Stored verbatim on the grant, so the reconnect prompt can tell a
#: security-driven disconnect apart from one the user performed deliberately.
PRIVACY_MODE_REASON = "Privacy Mode enabled"
PASSWORD_RESET_REASON = "Password reset"

#: How long after a reset the app keeps offering to help reconnect. Long enough
#: to survive a weekend and a holiday; short enough that a prompt nobody acted
#: on stops following them around forever.
RECONNECT_PROMPT_DAYS = 14

# Research OAuth grants share this table and belong to another product.
_WORKSPACE_ONLY = WorkspaceMCPGrant.client_id.not_like("research.%")


async def revoke_user_workspace_grants(
    db: AsyncSession,
    request: Request,
    *,
    user,
    reason: str,
) -> list[WorkspaceMCPGrant]:
    """Mark every active Workspace grant for ``user`` revoked, and audit it.

    Returns the grants that were revoked so the caller can clean up their
    runtime credentials *after* committing — the database row is authoritative,
    and an unreachable Redis must not be able to block the revocation itself.
    """

    from app.services.workspace_mcp_oauth import append_workspace_mcp_audit

    grants = list(
        (
            await db.scalars(
                select(WorkspaceMCPGrant)
                .where(
                    WorkspaceMCPGrant.tenant_id == user.tenant_id,
                    WorkspaceMCPGrant.user_id == user.id,
                    _WORKSPACE_ONLY,
                    WorkspaceMCPGrant.status == "active",
                    WorkspaceMCPGrant.revoked_at.is_(None),
                )
                .with_for_update()
            )
        ).all()
    )
    if not grants:
        return []

    revoked_at = datetime.now(timezone.utc)
    for grant in grants:
        grant.status = "revoked"
        grant.revoked_at = revoked_at
        grant.revoked_by_user_id = user.id
        grant.revocation_reason = reason
        await append_workspace_mcp_audit(
            db,
            request,
            tenant_id=user.tenant_id,
            user_id=user.id,
            grant_id=grant.id,
            client_id=grant.client_id,
            event_type="grant_revoked",
            outcome="success",
            metadata={"reason": reason},
        )
    return grants


async def cleanup_revoked_grant_runtime(
    request: Request, grants: list[WorkspaceMCPGrant]
) -> None:
    """Best-effort invalidation of live tokens for already-revoked grants.

    Call only after the revocation is committed. Every failure is logged and
    swallowed: the resource server re-reads the grant row before executing a
    tool, so a missed cache cleanup delays nothing beyond the access token's own
    short lifetime.
    """

    if not grants:
        return
    from app.services.workspace_mcp_oauth import revoke_workspace_grant_runtime

    for grant in grants:
        try:
            await revoke_workspace_grant_runtime(request, grant.id)
        except Exception:
            logger.exception(
                "Workspace MCP runtime credential cleanup failed after revocation",
                extra={"grant_id": str(grant.id)},
            )


async def pending_reconnect_clients(db: AsyncSession, user) -> list[dict]:
    """Assistants a password reset disconnected that are still not back.

    Self-clearing by construction: a client with a live grant again is not
    listed, so reconnecting is the thing that dismisses the prompt. Privacy Mode
    suppresses the list entirely — while it is on, reconnecting is refused
    anyway, and inviting someone to try would be telling them to do something
    the product will not let them do.
    """

    if getattr(user, "privacy_mode", False):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=RECONNECT_PROMPT_DAYS)
    revoked = list(
        (
            await db.scalars(
                select(WorkspaceMCPGrant)
                .where(
                    WorkspaceMCPGrant.tenant_id == user.tenant_id,
                    WorkspaceMCPGrant.user_id == user.id,
                    _WORKSPACE_ONLY,
                    WorkspaceMCPGrant.revocation_reason == PASSWORD_RESET_REASON,
                    WorkspaceMCPGrant.revoked_at.is_not(None),
                    WorkspaceMCPGrant.revoked_at >= cutoff,
                )
                .order_by(WorkspaceMCPGrant.revoked_at.desc())
            )
        ).all()
    )
    if not revoked:
        return []

    reconnected = set(
        (
            await db.scalars(
                select(WorkspaceMCPGrant.client_id).where(
                    WorkspaceMCPGrant.tenant_id == user.tenant_id,
                    WorkspaceMCPGrant.user_id == user.id,
                    _WORKSPACE_ONLY,
                    WorkspaceMCPGrant.status == "active",
                    WorkspaceMCPGrant.revoked_at.is_(None),
                )
            )
        ).all()
    )

    seen: set[str] = set()
    pending: list[dict] = []
    for grant in revoked:
        if grant.client_id in reconnected or grant.client_id in seen:
            continue
        seen.add(grant.client_id)
        pending.append(
            {
                "client_id": grant.client_id,
                "client_name": grant.client_name,
                "disconnected_at": grant.revoked_at.isoformat(),
            }
        )
    return pending
