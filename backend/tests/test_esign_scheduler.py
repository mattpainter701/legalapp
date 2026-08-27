from contextlib import asynccontextmanager

import pytest

from app.services import scheduler
from app.services.esign import notifications


@pytest.mark.asyncio
async def test_scheduler_runs_tenant_scoped_esign_reminders(monkeypatch):
    session = object()
    applied = []
    processed = []

    @asynccontextmanager
    async def session_maker():
        yield session

    async def apply_context(value):
        applied.append(value)

    async def process(value):
        processed.append(value)
        return 2

    monkeypatch.setattr(scheduler, "async_session_maker", session_maker)
    monkeypatch.setattr(scheduler, "_apply_scheduler_tenant_context", apply_context)
    monkeypatch.setattr(notifications, "process_due_reminders", process)

    job = scheduler.LegalScheduler()
    token = scheduler._scheduler_tenant_id.set("tenant-1")
    try:
        await job._check_esign_reminders()
    finally:
        scheduler._scheduler_tenant_id.reset(token)

    assert applied == [session]
    assert processed == [session]
