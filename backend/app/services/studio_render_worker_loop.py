"""Dependency-injected polling loop dedicated to Studio render job kinds."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

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


Sleep = Callable[[float], Awaitable[None]]


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
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not 1 <= batch_size <= 100:
            raise ValueError("Studio worker batch size must be between 1 and 100")
        if not 1 <= concurrency <= min(batch_size, 16):
            raise ValueError("Studio worker concurrency is invalid")
        if not 0.01 <= idle_seconds <= 60:
            raise ValueError("Studio worker idle interval is invalid")
        self.source = source
        self.worker = worker
        self.batch_size = batch_size
        self.concurrency = concurrency
        self.idle_seconds = idle_seconds
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

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            batch = asyncio.create_task(self.run_once())
            stop_wait = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    {batch, stop_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                if stop_wait in done and stop.is_set():
                    return
                processed = batch.result()
            finally:
                if not batch.done():
                    batch.cancel()
                if not stop_wait.done():
                    stop_wait.cancel()
                await asyncio.gather(batch, stop_wait, return_exceptions=True)
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
