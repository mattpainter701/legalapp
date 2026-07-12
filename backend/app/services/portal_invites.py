"""RLS-safe lookup for public, opaque portal invitation tokens."""

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import clear_tenant_context, set_tenant_context
from app.models.tenant import Tenant


InviteT = TypeVar("InviteT")

# Public invite failures deliberately use one status/detail so the endpoint is
# not an oracle for token validity, expiry, revocation, or tenant suspension.
PORTAL_INVITE_UNAVAILABLE_STATUS = 404
PORTAL_INVITE_UNAVAILABLE_DETAIL = "Invite not found or unavailable"


async def resolve_active_portal_invite(
    db: AsyncSession,
    invite_model: type[InviteT],
    token_hash: str,
) -> InviteT | None:
    """Resolve an opaque token without disabling tenant RLS.

    The old invite links contain only a high-entropy secret, so the tenant
    cannot be derived before lookup. The tenant registry itself is intentionally
    global; enumerate active tenants and enter exactly one ordinary tenant GUC
    at a time. A matching tenant receives a shared row lock through commit so a
    concurrent suspension cannot race a successful invitation acceptance.
    """

    await clear_tenant_context(db)
    tenant_ids = list(
        (
            await db.scalars(
                select(Tenant.id).where(Tenant.is_active.is_(True)).order_by(Tenant.id)
            )
        ).all()
    )
    for tenant_id in tenant_ids:
        await set_tenant_context(db, str(tenant_id))
        invite = await db.scalar(
            select(invite_model)
            .where(
                invite_model.tenant_id == tenant_id,
                invite_model.token_hash == token_hash,
            )
            .with_for_update()
        )
        if invite is None:
            continue

        # Re-check and lock just the matching tenant. If it was suspended after
        # enumeration, make the invite indistinguishable from any invalid token.
        active_tenant = await db.scalar(
            select(Tenant)
            .where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
            .with_for_update(read=True)
        )
        if active_tenant is not None:
            return invite
        await clear_tenant_context(db)
        return None

    await clear_tenant_context(db)
    return None
