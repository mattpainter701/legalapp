"""Dependency-injected polling loop dedicated to Studio render job kinds."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import set_tenant_context
from app.models.durable_job import DurableJob
from app.models.tenant import Tenant
from app.schemas.studio_render import STUDIO_RENDER_JOB_KINDS, StudioRenderJobKind


@dataclass(frozen=True)
class StudioRenderWorkItem:
    tenant_id: uuid.UUID
    job_id: uuid.UUID
    kind: StudioRenderJobKind

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", uuid.UUID(str(self.tenant_id)))
        object.__setattr__(self, "job_id", uuid.UUID(str(self.job_id)))
        if self.kind not in STUDIO_RENDER_JOB_KINDS:
            raise ValueError("worker item is not a Studio render job")


class StudioRenderWorkSource(Protocol):
    async def next_batch(self, *, limit: int) -> Sequence[StudioRenderWorkItem]: ...


class StudioRenderWorkProcessor(Protocol):
    async def process(self, job_id: uuid.UUID, tenant_id: uuid.UUID) -> bool: ...


class StudioRenderMaintenanceTask(Protocol):
    async def run_once(self) -> int: ...


Sleep = Callable[[float], Awaitable[None]]
RuntimeHeartbeat = Callable[[bool], Awaitable[None]]


logger = logging.getLogger(__name__)


class PostgresStudioRenderWorkSource:
    """Fair, tenant-bound discovery; authoritative claiming remains fenced."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        tenant_scan_batch: int = 25,
    ) -> None:
        if not 1 <= tenant_scan_batch <= 500:
            raise ValueError("Studio tenant scan batch is invalid")
        self.session_factory = session_factory
        self.tenant_scan_batch = tenant_scan_batch
        self._tenant_cursor: uuid.UUID | None = None

    async def _tenant_page(
        self,
        *,
        after: uuid.UUID | None,
        through: uuid.UUID | None,
        limit: int,
    ) -> tuple[uuid.UUID, ...]:
        async with self.session_factory() as db:
            query = select(Tenant.id).where(Tenant.is_active.is_(True))
            if after is not None:
                query = query.where(Tenant.id > after)
            if through is not None:
                query = query.where(Tenant.id <= through)
            return tuple(
                (await db.scalars(query.order_by(Tenant.id).limit(limit))).all()
            )

    async def _tenant_ids(self) -> tuple[uuid.UUID, ...]:
        first = await self._tenant_page(
            after=self._tenant_cursor,
            through=None,
            limit=self.tenant_scan_batch,
        )
        remaining = self.tenant_scan_batch - len(first)
        if remaining <= 0 or self._tenant_cursor is None:
            return first
        wrapped = await self._tenant_page(
            after=None,
            through=self._tenant_cursor,
            limit=remaining,
        )
        return first + wrapped

    async def _jobs_for_tenant(
        self, tenant_id: uuid.UUID, *, limit: int
    ) -> tuple[tuple[uuid.UUID, StudioRenderJobKind], ...]:
        async with self.session_factory() as db:
            await set_tenant_context(db, str(tenant_id))
            rows = (
                await db.execute(
                    select(DurableJob.id, DurableJob.kind)
                    .where(
                        DurableJob.tenant_id == tenant_id,
                        DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
                        DurableJob.status.in_(
                            {"pending", "running", "cancel_requested"}
                        ),
                        or_(
                            DurableJob.status != "pending",
                            DurableJob.available_at <= func.clock_timestamp(),
                        ),
                    )
                    .order_by(
                        case(
                            (DurableJob.status == "cancel_requested", 0),
                            (DurableJob.status == "pending", 1),
                            else_=2,
                        ),
                        DurableJob.leased_at,
                        DurableJob.available_at,
                        DurableJob.created_at,
                        DurableJob.id,
                    )
                    .limit(limit)
                )
            ).all()
            await db.rollback()
        return tuple(rows)

    async def next_batch(self, *, limit: int) -> Sequence[StudioRenderWorkItem]:
        if not 1 <= limit <= 100:
            raise ValueError("Studio work-source limit must be between 1 and 100")
        items: list[StudioRenderWorkItem] = []
        tenant_ids = await self._tenant_ids()
        for tenant_id in tenant_ids:
            rows = await self._jobs_for_tenant(tenant_id, limit=limit - len(items))
            items.extend(
                StudioRenderWorkItem(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    kind=kind,
                )
                for job_id, kind in rows
            )
            self._tenant_cursor = tenant_id
            if len(items) >= limit:
                break
        return tuple(items)


class StudioRenderWorkerLoop:
    """Bounded cooperative loop with no scheduler or generic-worker coupling."""

    def __init__(
        self,
        *,
        source: StudioRenderWorkSource,
        worker: StudioRenderWorkProcessor,
        batch_size: int = 8,
        concurrency: int = 2,
        idle_seconds: float = 1.0,
        maintenance: StudioRenderMaintenanceTask | None = None,
        maintenance_interval_seconds: float = 300.0,
        runtime_heartbeat: RuntimeHeartbeat | None = None,
        runtime_heartbeat_interval_seconds: float = 15.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not 1 <= batch_size <= 100:
            raise ValueError("Studio worker batch size must be between 1 and 100")
        if not 1 <= concurrency <= min(batch_size, 16):
            raise ValueError("Studio worker concurrency is invalid")
        if not 0.01 <= idle_seconds <= 60:
            raise ValueError("Studio worker idle interval is invalid")
        if not 10 <= maintenance_interval_seconds <= 86_400:
            raise ValueError("Studio maintenance interval is invalid")
        if not 5 <= runtime_heartbeat_interval_seconds <= 60:
            raise ValueError("Studio runtime heartbeat interval is invalid")
        self.source = source
        self.worker = worker
        self.batch_size = batch_size
        self.concurrency = concurrency
        self.idle_seconds = idle_seconds
        self.maintenance = maintenance
        self.maintenance_interval_seconds = float(maintenance_interval_seconds)
        self.runtime_heartbeat = runtime_heartbeat
        self.runtime_heartbeat_interval_seconds = float(
            runtime_heartbeat_interval_seconds
        )
        self.monotonic = monotonic
        self._next_maintenance_at = monotonic()
        self._consecutive_maintenance_failures = 0
        self.sleep = sleep

    async def run_once(self) -> int:
        items = tuple(await self.source.next_batch(limit=self.batch_size))
        if len(items) > self.batch_size:
            raise RuntimeError("Studio work source exceeded its batch bound")
        if len({(item.tenant_id, item.job_id) for item in items}) != len(items):
            raise RuntimeError("Studio work source returned duplicate jobs")
        if any(item.kind not in STUDIO_RENDER_JOB_KINDS for item in items):
            raise RuntimeError("Studio work source returned a foreign job kind")

        semaphore = asyncio.Semaphore(self.concurrency)

        async def process(item: StudioRenderWorkItem) -> bool:
            async with semaphore:
                return await self.worker.process(item.job_id, item.tenant_id)

        if not items:
            return 0
        outcomes = await asyncio.gather(*(process(item) for item in items))
        return sum(outcome is True for outcome in outcomes)

    async def _publish_runtime_heartbeat(self, stop: asyncio.Event) -> None:
        if self.runtime_heartbeat is None:
            return
        while not stop.is_set():
            await self.runtime_heartbeat(self._consecutive_maintenance_failures < 3)
            stop_wait = asyncio.create_task(stop.wait())
            interval = asyncio.create_task(
                self.sleep(self.runtime_heartbeat_interval_seconds)
            )
            try:
                done, _ = await asyncio.wait(
                    {stop_wait, interval}, return_when=asyncio.FIRST_COMPLETED
                )
                if stop_wait in done and stop.is_set():
                    return
                await interval
            finally:
                if not stop_wait.done():
                    stop_wait.cancel()
                if not interval.done():
                    interval.cancel()
                await asyncio.gather(stop_wait, interval, return_exceptions=True)

    async def _run_until_stopped(
        self,
        stop: asyncio.Event,
        heartbeat: asyncio.Task[None] | None,
    ) -> None:
        while not stop.is_set():
            batch = asyncio.create_task(self.run_once())
            stop_wait = asyncio.create_task(stop.wait())
            try:
                waiters = {batch, stop_wait}
                if heartbeat is not None:
                    waiters.add(heartbeat)
                done, _ = await asyncio.wait(
                    waiters, return_when=asyncio.FIRST_COMPLETED
                )
                if stop_wait in done and stop.is_set():
                    return
                if heartbeat is not None and heartbeat in done:
                    await heartbeat
                    raise RuntimeError("Studio runtime heartbeat stopped unexpectedly")
                processed = batch.result()
            finally:
                if not batch.done():
                    batch.cancel()
                if not stop_wait.done():
                    stop_wait.cancel()
                await asyncio.gather(batch, stop_wait, return_exceptions=True)
            if (
                self.maintenance is not None
                and self.monotonic() >= self._next_maintenance_at
            ):
                try:
                    await self.maintenance.run_once()
                except Exception:
                    self._consecutive_maintenance_failures = min(
                        self._consecutive_maintenance_failures + 1, 3
                    )
                    logger.warning(
                        "Studio render maintenance failed (%d consecutive failures)",
                        self._consecutive_maintenance_failures,
                    )
                else:
                    self._consecutive_maintenance_failures = 0
                self._next_maintenance_at = (
                    self.monotonic() + self.maintenance_interval_seconds
                )
            if processed:
                continue
            idle = asyncio.create_task(self.sleep(self.idle_seconds))
            stop_wait = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    {idle, stop_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                if stop_wait in done and stop.is_set():
                    return
                await idle
            finally:
                if not idle.done():
                    idle.cancel()
                if not stop_wait.done():
                    stop_wait.cancel()
                await asyncio.gather(idle, stop_wait, return_exceptions=True)

    async def run_forever(self, stop: asyncio.Event) -> None:
        heartbeat = (
            asyncio.create_task(self._publish_runtime_heartbeat(stop))
            if self.runtime_heartbeat is not None
            else None
        )
        try:
            await self._run_until_stopped(stop, heartbeat)
        finally:
            if heartbeat is not None and not heartbeat.done():
                heartbeat.cancel()
            if heartbeat is not None:
                await asyncio.gather(heartbeat, return_exceptions=True)
