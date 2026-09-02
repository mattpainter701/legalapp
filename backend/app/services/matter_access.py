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
    # correlate_except pins each subquery's own FROM. This predicate is embedded
    # in whatever query the caller is building, and matter_id_column belongs to
    # that outer query, so SQLAlchemy auto-correlation is what makes the
    # reference work. But a caller that already joins Matter -- the overdue
    # tasks report does, to read matter_name -- makes Matter an outer FROM too,
    # and auto-correlation then hoists the subquery's own Matter out with it,
    # leaving a SELECT with no FROM at all: "returned no FROM clauses due to
    # auto-correlation". Naming the table here keeps it local without
    # suppressing the correlation the predicate depends on.
    if is_admin:
        return exists(
            select(Matter.id)
            .where(
                Matter.tenant_id == tenant_id,
                Matter.id == matter_id_column,
            )
            .correlate_except(Matter)
        )
    return or_(
        exists(
            select(Matter.id)
            .where(
                Matter.tenant_id == tenant_id,
                Matter.id == matter_id_column,
                Matter.user_id == user_id,
            )
            .correlate_except(Matter)
        ),
        exists(
            select(MatterAssignment.id)
            .where(
                MatterAssignment.tenant_id == tenant_id,
                MatterAssignment.matter_id == matter_id_column,
                MatterAssignment.user_id == user_id,
            )
            .correlate_except(MatterAssignment)
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
