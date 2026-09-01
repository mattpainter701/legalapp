"""Database-free checks for the Studio-only dependency-injected worker loop."""

import asyncio
import uuid

import pytest

from app.schemas.studio_render import STUDIO_RENDER_JOB_KINDS
from app.services.studio_render_worker_loop import (
    StudioRenderWorkerLoop,
    StudioRenderWorkItem,
)


class _Source:
    def __init__(self, items):
        self.items = tuple(items)
        self.limits = []

    async def next_batch(self, *, limit):
        self.limits.append(limit)
        items, self.items = self.items[:limit], self.items[limit:]
        return items


class _Worker:
    def __init__(self):
        self.seen = []
        self.active = 0
        self.max_active = 0

    async def process(self, job_id, tenant_id):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.seen.append((tenant_id, job_id))
        self.active -= 1
        return True


def _item(kind):
    return StudioRenderWorkItem(
        tenant_id=uuid.uuid4(), job_id=uuid.uuid4(), kind=kind
    )


@pytest.mark.asyncio
async def test_loop_dispatches_only_all_four_studio_kinds_with_bounds():
    items = [_item(kind) for kind in sorted(STUDIO_RENDER_JOB_KINDS)]
    source = _Source(items)
    worker = _Worker()
    loop = StudioRenderWorkerLoop(
        source=source,
        worker=worker,
        batch_size=4,
        concurrency=2,
    )
    assert await loop.run_once() == 4
    assert source.limits == [4]
    assert set(worker.seen) == {(item.tenant_id, item.job_id) for item in items}
    assert worker.max_active <= 2


@pytest.mark.asyncio
async def test_loop_rejects_duplicate_work_items():
    item = _item("studio_test_render")
    loop = StudioRenderWorkerLoop(
        source=_Source([item, item]), worker=_Worker(), batch_size=2
    )
    with pytest.raises(RuntimeError, match="duplicate"):
        await loop.run_once()


@pytest.mark.asyncio
async def test_stop_cancels_and_drains_an_active_processor_batch():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingWorker:
        async def process(self, _job_id, _tenant_id):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    stop = asyncio.Event()
    loop = StudioRenderWorkerLoop(
        source=_Source([_item("studio_test_render")]),
        worker=BlockingWorker(),
        batch_size=1,
        concurrency=1,
    )
    task = asyncio.create_task(loop.run_forever(stop))
    await asyncio.wait_for(started.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_runner_cancellation_cancels_and_drains_batch_and_stop_wait():
    processor_started = asyncio.Event()
    processor_cancelled = asyncio.Event()
    stop_wait_started = asyncio.Event()
    stop_wait_cancelled = asyncio.Event()

    class BlockingWorker:
        async def process(self, _job_id, _tenant_id):
            processor_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                processor_cancelled.set()

    class TrackingStop:
        def is_set(self):
            return False

        async def wait(self):
            stop_wait_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stop_wait_cancelled.set()

    loop = StudioRenderWorkerLoop(
        source=_Source([_item("studio_test_render")]),
        worker=BlockingWorker(),
        batch_size=1,
        concurrency=1,
    )
    task = asyncio.create_task(loop.run_forever(TrackingStop()))
    await asyncio.wait_for(processor_started.wait(), timeout=1)
    await asyncio.wait_for(stop_wait_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processor_cancelled.is_set()
    assert stop_wait_cancelled.is_set()


def test_work_item_rejects_foreign_durable_job_kind():
    with pytest.raises(ValueError, match="not a Studio render job"):
        StudioRenderWorkItem(
            tenant_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            kind="send_email",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_maintenance_uses_monotonic_interval_not_each_idle_poll():
    stop = asyncio.Event()
    clock = [0.0]

    class Maintenance:
        def __init__(self):
            self.calls = 0

        async def run_once(self):
            self.calls += 1
            return 0

    maintenance = Maintenance()

    async def idle(_seconds):
        clock[0] += 5
        if clock[0] >= 15:
            stop.set()

    loop = StudioRenderWorkerLoop(
        source=_Source([]),
        worker=_Worker(),
        maintenance=maintenance,
        maintenance_interval_seconds=10,
        monotonic=lambda: clock[0],
        sleep=idle,
    )
    await loop.run_forever(stop)
    assert maintenance.calls == 2
