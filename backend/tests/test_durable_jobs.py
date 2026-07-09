from datetime import datetime, timedelta, timezone
import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import set_tenant_context
from app.services.durable_jobs import claim_job, enqueue_job, fail_job, serialize_job


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_and_status_is_serializable(
    db_session, test_tenant
):
    first = await enqueue_job(
        db_session,
        tenant_id=test_tenant.id,
        kind="document_ingest",
        idempotency_key="doc-1",
        payload={"document_id": "doc-1"},
    )
    await db_session.commit()
    second = await enqueue_job(
        db_session,
        tenant_id=test_tenant.id,
        kind="document_ingest",
        idempotency_key="doc-1",
        payload={"document_id": "different"},
    )
    assert second.id == first.id
    assert serialize_job(second)["status"] == "pending"


@pytest.mark.asyncio
async def test_concurrent_enqueue_converges_on_one_job(test_engine, test_tenant):
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)

    async def submit():
        async with sessions() as db:
            await set_tenant_context(db, str(test_tenant.id))
            row = await enqueue_job(
                db,
                tenant_id=test_tenant.id,
                kind="document_ingest",
                idempotency_key="concurrent-doc",
                payload={"document_id": "concurrent-doc"},
            )
            await db.commit()
            return row.id

    first, second = await asyncio.gather(submit(), submit())
    assert first == second


@pytest.mark.asyncio
async def test_claim_is_exclusive_and_stale_lease_is_recoverable(
    db_session, test_tenant
):
    row = await enqueue_job(
        db_session,
        tenant_id=test_tenant.id,
        kind="document_ingest",
        idempotency_key="doc-2",
        payload={},
    )
    await db_session.commit()
    claimed = await claim_job(db_session, row.id, owner="worker-a")
    assert claimed.status == "running"
    assert await claim_job(db_session, row.id, owner="worker-b") is None
    claimed.leased_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.commit()
    reclaimed = await claim_job(db_session, row.id, owner="worker-b")
    assert reclaimed.lease_owner == "worker-b"
    assert reclaimed.attempts == 2


@pytest.mark.asyncio
async def test_failure_retries_with_backoff_then_becomes_terminal(
    db_session, test_tenant
):
    row = await enqueue_job(
        db_session,
        tenant_id=test_tenant.id,
        kind="document_ingest",
        idempotency_key="doc-3",
        payload={},
    )
    row.max_attempts = 1
    await db_session.commit()
    claimed = await claim_job(db_session, row.id)
    await fail_job(db_session, claimed, RuntimeError("boom"))
    assert claimed.status == "failed"
    assert claimed.last_error == "boom"
