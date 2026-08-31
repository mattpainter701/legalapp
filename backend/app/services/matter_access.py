"""Canonical owner/assignment/admin visibility for matter-bound records."""

from __future__ import annotations

import uuid

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter


def matter_access_predicate(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    is_admin: bool,
    matter_id_column,
):
    if is_admin:
        return exists(
            select(Matter.id).where(
                Matter.tenant_id == tenant_id,
                Matter.id == matter_id_column,
            )
        )
    return or_(
        exists(
            select(Matter.id).where(
                Matter.tenant_id == tenant_id,
                Matter.id == matter_id_column,
                Matter.user_id == user_id,
            )
        ),
        exists(
            select(MatterAssignment.id).where(
                MatterAssignment.tenant_id == tenant_id,
                MatterAssignment.matter_id == matter_id_column,
                MatterAssignment.user_id == user_id,
            )
        ),
    )


async def can_access_matter(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    is_admin: bool,
    matter_id: uuid.UUID,
) -> bool:
    if is_admin:
        return bool(
            await db.scalar(
                select(Matter.id).where(
                    Matter.tenant_id == tenant_id,
                    Matter.id == matter_id,
                )
            )
        )
    return bool(
        await db.scalar(
            select(Matter.id).where(
                Matter.tenant_id == tenant_id,
                Matter.id == matter_id,
                or_(
                    Matter.user_id == user_id,
                    exists(
                        select(MatterAssignment.id).where(
                            MatterAssignment.tenant_id == tenant_id,
                            MatterAssignment.matter_id == Matter.id,
                            MatterAssignment.user_id == user_id,
                        )
                    ),
                ),
            )
        )
    )


async def accessible_matter_ids(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    is_admin: bool,
) -> set[uuid.UUID] | None:
    if is_admin:
        return None
    owned = select(Matter.id).where(
        Matter.tenant_id == tenant_id,
        Matter.user_id == user_id,
    )
    assigned = select(MatterAssignment.matter_id).where(
        MatterAssignment.tenant_id == tenant_id,
        MatterAssignment.user_id == user_id,
    )
    return set((await db.scalars(owned.union(assigned))).all())
