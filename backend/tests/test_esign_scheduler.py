from contextlib import asynccontextmanager

import pytest

from app.services import scheduler
from app.services.esign import notifications
from app.services.esign import service as esign_service


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


@pytest.mark.asyncio
async def test_scheduler_retries_pending_esign_completions_per_tenant(monkeypatch):
    session = object()
    applied = []
    retried = []

    @asynccontextmanager
    async def session_maker():
        yield session

    async def apply_context(value):
        applied.append(value)

    async def retry(value):
        retried.append(value)
        return 1

    monkeypatch.setattr(scheduler, "async_session_maker", session_maker)
    monkeypatch.setattr(scheduler, "_apply_scheduler_tenant_context", apply_context)
    monkeypatch.setattr(esign_service, "retry_pending_completions", retry)

    job = scheduler.LegalScheduler()
    token = scheduler._scheduler_tenant_id.set("tenant-1")
    try:
        await job._complete_pending_esign()
    finally:
        scheduler._scheduler_tenant_id.reset(token)

    assert applied == [session]
    assert retried == [session]


def test_pending_completion_job_is_registered_every_five_minutes(monkeypatch):
    registered = []

    class FakeScheduler:
        running = False

        def add_job(self, _func, *args, **kwargs):
            registered.append((args, kwargs))

        def start(self):
            pass

    monkeypatch.setattr(scheduler.settings, "CLOUD_SEARCH_ENABLED", True)
    instance = scheduler.LegalScheduler()
    monkeypatch.setattr(instance, "scheduler", FakeScheduler())
    instance.start()

    job = next(
        kwargs
        for _args, kwargs in registered
        if kwargs["id"] == "esign-complete-pending"
    )
    assert job["minutes"] == 5
