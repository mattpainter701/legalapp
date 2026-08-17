"""Atomic reservation accounting for disposable-demo AI operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.demo_session import DemoSession, DemoUsageReservation


class DemoQuotaExceeded(RuntimeError):
    pass


class DemoOperationDuplicate(RuntimeError):
    pass


@dataclass(frozen=True)
class DemoReservation:
    id: uuid.UUID
    tenant_id: uuid.UUID


async def reserve_demo_operation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    idempotency_key: str,
    surface: str,
) -> DemoReservation | None:
    await set_tenant_context(db, str(tenant_id))
    session = await db.scalar(
        select(DemoSession).where(
            DemoSession.tenant_id == tenant_id,
            DemoSession.status == "active",
            DemoSession.expires_at > datetime.now(timezone.utc),
        )
    )
    if session is None:
        return None
    reservation_id = uuid.uuid4()
    inserted = await db.scalar(
        insert(DemoUsageReservation)
        .values(
            id=reservation_id,
            tenant_id=tenant_id,
            session_id=session.id,
            idempotency_key=idempotency_key[:120],
            surface=surface[:40],
            status="reserved",
        )
        .on_conflict_do_nothing(index_elements=["session_id", "idempotency_key"])
        .returning(DemoUsageReservation.id)
    )
    if inserted is None:
        await db.rollback()
        raise DemoOperationDuplicate("This demo operation was already submitted")
    claimed = await db.scalar(
        update(DemoSession)
        .where(
            DemoSession.id == session.id,
            DemoSession.used + DemoSession.reserved < DemoSession.quota,
        )
        .values(reserved=DemoSession.reserved + 1)
        .returning(DemoSession.id)
    )
    if claimed is None:
        await db.execute(
            delete(DemoUsageReservation).where(
                DemoUsageReservation.id == reservation_id
            )
        )
        await db.commit()
        raise DemoQuotaExceeded("This demo has used all available AI operations")
    await db.commit()
    return DemoReservation(id=reservation_id, tenant_id=tenant_id)


async def settle_demo_operation(db: AsyncSession, reservation: DemoReservation) -> None:
    await set_tenant_context(db, str(reservation.tenant_id))
    session_id = await db.scalar(
        update(DemoUsageReservation)
        .where(
            DemoUsageReservation.id == reservation.id,
            DemoUsageReservation.status == "reserved",
        )
        .values(status="settled", settled_at=datetime.now(timezone.utc))
        .returning(DemoUsageReservation.session_id)
    )
    if session_id is not None:
        await db.execute(
            update(DemoSession)
            .where(DemoSession.id == session_id, DemoSession.reserved > 0)
            .values(
                reserved=DemoSession.reserved - 1,
                used=DemoSession.used + 1,
            )
        )
    await db.commit()


async def release_demo_operation(
    db: AsyncSession, reservation: DemoReservation
) -> None:
    await set_tenant_context(db, str(reservation.tenant_id))
    session_id = await db.scalar(
        update(DemoUsageReservation)
        .where(
            DemoUsageReservation.id == reservation.id,
            DemoUsageReservation.status == "reserved",
        )
        .values(status="released", settled_at=datetime.now(timezone.utc))
        .returning(DemoUsageReservation.session_id)
    )
    if session_id is not None:
        await db.execute(
            update(DemoSession)
            .where(DemoSession.id == session_id, DemoSession.reserved > 0)
            .values(reserved=DemoSession.reserved - 1)
        )
    await db.commit()
