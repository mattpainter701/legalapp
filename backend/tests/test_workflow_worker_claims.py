from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import durable_job_worker as worker
from app.services import workflow_runtime as runtime


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def prepare(monkeypatch, *, exhausted=False, error=False, stale=False, maxed=False):
    row = NS(
        id=uuid4(),
        tenant_id=uuid4(),
        kind="workflow_run",
        status="running",
        attempts=3 if maxed else 1,
        max_attempts=3,
        leased_at=datetime.now(timezone.utc),
        payload={"run_id": str(uuid4())},
    )
    db = Session()
    db.rollback = AsyncMock()
    db.scalar = AsyncMock(
        side_effect=[row] if exhausted else [None, True, None if stale else row]
    )
    monkeypatch.setattr(worker, "async_session_maker", lambda: db)
    monkeypatch.setattr(worker, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(worker, "claim_job", AsyncMock(return_value=row))
    execute = AsyncMock(
        side_effect=ValueError("private handler detail") if error else None,
        return_value={"outcome": "awaiting_review"},
    )
    monkeypatch.setattr(worker, "resolve_job_handler", lambda _: NS(execute=execute))
    finish, fail, block = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(worker, "finish_job", finish)
    monkeypatch.setattr(worker, "fail_job", fail)
    monkeypatch.setattr(runtime, "block_exhausted_run", block)
    return row, db, execute, finish, fail, block


@pytest.mark.asyncio
async def test_exhausted_runtime_is_visible_without_reexecution(monkeypatch):
    row, db, execute, finish, fail, block = prepare(
        monkeypatch, exhausted=True, maxed=True
    )
    assert await worker.process_job(row.id, row.tenant_id)
    block.assert_awaited_once_with(db, row)
    assert fail.call_args.kwargs["retryable"] is False
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_completion_never_overwrites_new_worker(monkeypatch):
    row, db, execute, finish, fail, block = prepare(monkeypatch, stale=True)
    assert not await worker.process_job(row.id, row.tenant_id)
    finish.assert_not_awaited()
    fail.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stale,maxed", [(False, False), (True, False), (False, True)])
async def test_runtime_failure_is_redacted_and_claim_checked(monkeypatch, stale, maxed):
    row, db, execute, finish, fail, block = prepare(
        monkeypatch, error=True, stale=stale, maxed=maxed
    )
    assert await worker.process_job(row.id, row.tenant_id) is (not stale)
    db.rollback.assert_awaited_once()
    if stale:
        fail.assert_not_awaited()
    else:
        assert "private" not in str(fail.call_args.args[2])
        assert block.await_count == int(maxed)
