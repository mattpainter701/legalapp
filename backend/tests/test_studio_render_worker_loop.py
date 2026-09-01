"""Database-free checks for the Studio-only dependency-injected worker loop."""

import asyncio
import uuid

import pytest

from app.schemas.studio_render import STUDIO_RENDER_JOB_KINDS
from app.services.studio_render_worker_loop import (
    PostgresStudioRenderWorkSource,
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
    return StudioRenderWorkItem(tenant_id=uuid.uuid4(), job_id=uuid.uuid4(), kind=kind)


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
async def test_runtime_heartbeat_continues_during_long_processor_batch():
    processor_started = asyncio.Event()
    processor_cancelled = asyncio.Event()
    second_heartbeat = asyncio.Event()
    heartbeat_calls = 0

    class BlockingWorker:
        async def process(self, _job_id, _tenant_id):
            processor_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                processor_cancelled.set()

    async def heartbeat(healthy):
        nonlocal heartbeat_calls
        assert healthy is True
        heartbeat_calls += 1
        if heartbeat_calls >= 2:
            second_heartbeat.set()

    async def immediate_sleep(_seconds):
        await asyncio.sleep(0)

    stop = asyncio.Event()
    loop = StudioRenderWorkerLoop(
        source=_Source([_item("studio_test_render")]),
        worker=BlockingWorker(),
        batch_size=1,
        concurrency=1,
        runtime_heartbeat=heartbeat,
        runtime_heartbeat_interval_seconds=5,
        sleep=immediate_sleep,
    )
    task = asyncio.create_task(loop.run_forever(stop))
    await asyncio.wait_for(processor_started.wait(), timeout=1)
    await asyncio.wait_for(second_heartbeat.wait(), timeout=1)
    assert heartbeat_calls >= 2
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert processor_cancelled.is_set()


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


@pytest.mark.asyncio
async def test_work_source_bounds_idle_tenant_scan_and_wraps_fairly():
    tenants = tuple(uuid.UUID(int=value) for value in range(1, 11))

    class BoundedSource(PostgresStudioRenderWorkSource):
        def __init__(self):
            super().__init__(None, tenant_scan_batch=3)  # type: ignore[arg-type]
            self.page_calls = []
            self.job_calls = []

        async def _tenant_page(self, *, after, through, limit):
            self.page_calls.append((after, through, limit))
            eligible = [
                tenant_id
                for tenant_id in tenants
                if (after is None or tenant_id > after)
                and (through is None or tenant_id <= through)
            ]
            return tuple(eligible[:limit])

        async def _jobs_for_tenant(self, tenant_id, *, limit):
            self.job_calls.append(tenant_id)
            return ()

    source = BoundedSource()
    per_poll = []
    for _ in range(4):
        before = len(source.job_calls)
        assert await source.next_batch(limit=8) == ()
        per_poll.append(len(source.job_calls) - before)
    assert per_poll == [3, 3, 3, 3]
    assert set(source.job_calls[:10]) == set(tenants)
    assert source.job_calls[:10] == list(tenants)
    assert all(call[2] <= 3 for call in source.page_calls)


@pytest.mark.asyncio
async def test_sustained_maintenance_failure_marks_runtime_heartbeat_unhealthy():
    states = []
    stop = asyncio.Event()

    async def heartbeat(healthy):
        states.append(healthy)
        stop.set()

    loop = StudioRenderWorkerLoop(
        source=_Source([]),
        worker=_Worker(),
        runtime_heartbeat=heartbeat,
    )
    loop._consecutive_maintenance_failures = 3
    await loop._publish_runtime_heartbeat(stop)
    assert states == [False]
