"""Postgres-backed job queue with idempotency, leases, retries and recovery."""

import socket
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.durable_job import DurableJob


async def enqueue_job(
    db: AsyncSession, *, tenant_id, kind: str, idempotency_key: str, payload: dict
) -> DurableJob:
    tenant_id = uuid.UUID(str(tenant_id))
    existing = await db.scalar(
        select(DurableJob).where(
            DurableJob.tenant_id == tenant_id,
            DurableJob.kind == kind,
            DurableJob.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    row = DurableJob(
        tenant_id=tenant_id, kind=kind, idempotency_key=idempotency_key, payload=payload
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return row
    except IntegrityError:
        # A concurrent request won the unique key. The savepoint preserves the
        # caller's transaction; return the canonical row.
        return await db.scalar(
            select(DurableJob).where(
                DurableJob.tenant_id == tenant_id,
                DurableJob.kind == kind,
                DurableJob.idempotency_key == idempotency_key,
            )
        )


async def get_tenant_job(db: AsyncSession, tenant_id, job_id) -> DurableJob | None:
    return await db.scalar(
        select(DurableJob).where(
            DurableJob.id == job_id, DurableJob.tenant_id == tenant_id
        )
    )


async def claim_job(
    db: AsyncSession, job_id, *, lease_seconds: int = 900, owner: str | None = None
) -> DurableJob | None:
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=lease_seconds)
    stmt = (
        select(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.attempts < DurableJob.max_attempts,
            DurableJob.available_at <= now,
            or_(
                DurableJob.status == "pending",
                (DurableJob.status == "running") & (DurableJob.leased_at < stale),
            ),
        )
        .with_for_update(skip_locked=True)
    )
    row = await db.scalar(stmt)
    if not row:
        return None
    row.status = "running"
    row.attempts += 1
    row.leased_at = now
    row.lease_owner = owner or socket.gethostname()
    row.last_error = None
    await db.commit()
    return row


async def finish_job(
    db: AsyncSession, row: DurableJob, *, result: dict | None = None
) -> None:
    row.status = "completed"
    row.progress = 100
    row.result = result or {}
    row.completed_at = datetime.now(timezone.utc)
    row.leased_at = None
    row.lease_owner = None
    await db.commit()


async def fail_job(db: AsyncSession, row: DurableJob, exc: Exception) -> None:
    row.last_error = str(exc)[:4000]
    row.leased_at = None
    row.lease_owner = None
    if row.attempts >= row.max_attempts:
        row.status = "failed"
    else:
        row.status = "pending"
        row.available_at = datetime.now(timezone.utc) + timedelta(
            seconds=min(3600, 2**row.attempts * 15)
        )
    await db.commit()


def serialize_job(row: DurableJob) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "status": row.status,
        "progress": row.progress,
        "attempts": row.attempts,
        "max_attempts": row.max_attempts,
        "last_error": row.last_error,
        "result": row.result,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
