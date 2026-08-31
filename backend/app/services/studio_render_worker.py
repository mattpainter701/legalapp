"""Worker facade for fenced Studio jobs; renderer implementations are injected."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Literal, Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import set_tenant_context
from app.models.studio_draft import StudioDraft, StudioDraftSnapshot
from app.services.studio_drafts import StudioError, StudioSourceRegistry
from app.services.studio_object_storage import (
    StudioObjectRef,
    StudioObjectStore,
    StudioStorageError,
)
from app.services.studio_render_jobs import (
    StudioInputBindingResolver,
    StudioJobLease,
    StudioRenderJobService,
    StudioRenderServiceError,
    default_worker_owner,
)
from app.services.studio_worker_isolation import (
    StudioIsolationError,
    StudioTrustedProcessorAdapter,
)


_PhaseResult = TypeVar("_PhaseResult")


def _isolation_failure(code: str) -> tuple[str, bool]:
    if code == "processor_timeout":
        return "processor_timeout", True
    if code == "hostile_input":
        return "hostile_input", False
    if code == "input_too_large":
        return "input_too_large", False
    if code == "processor_output_limit":
        return "output_too_large", False
    return "processor_unavailable", True


@dataclass(frozen=True)
class StudioProcessorOutput:
    content: bytes
    content_sha256: str
    media_type: str
    artifact_kind: str
    renderer_identity: str
    converter_identity: str
    validator_identity: str
    retention_class: str = "review"


class StudioProcessor(Protocol):
    """A renderer bound to a server-owned Phase 3 isolation policy."""

    isolation_policy_id: str
    isolation_enforced: Literal[True]

    async def process(
        self,
        *,
        source: bytes,
        snapshot: dict,
        options: dict,
        input_binding: bytes | None,
    ) -> StudioProcessorOutput: ...

    async def terminate(self) -> None:
        """Kill the isolated process tree and release its bounded workspace."""

        ...


class StudioRenderWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        object_store: StudioObjectStore,
        processors: dict[str, StudioProcessor],
        input_bindings: StudioInputBindingResolver | None = None,
        owner: str | None = None,
        lease_seconds: int = 900,
        heartbeat_seconds: int = 60,
        processor_timeout_seconds: int = 300,
        artifact_ttl_seconds: int = 86_400,
    ):
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        if not 5 <= heartbeat_seconds < lease_seconds / 2:
            raise ValueError("heartbeat_seconds must be below half the lease")
        if not 5 <= processor_timeout_seconds <= 1800:
            raise ValueError("processor timeout must be between 5 and 1800 seconds")
        if not 300 <= artifact_ttl_seconds <= 604_800:
            raise ValueError("artifact TTL must be between five minutes and seven days")
        if any(
            type(processor) is not StudioTrustedProcessorAdapter
            or not str(getattr(processor, "isolation_policy_id", "")).strip()
            or getattr(processor, "isolation_enforced", False) is not True
            for processor in processors.values()
        ):
            raise ValueError("every Studio processor requires enforced isolation")
        self.session_factory = session_factory
        self.object_store = object_store
        self.processors = dict(processors)
        self.input_bindings = input_bindings
        self.owner = owner or default_worker_owner()
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.processor_timeout_seconds = processor_timeout_seconds
        self.artifact_ttl_seconds = artifact_ttl_seconds

    async def _heartbeat(
        self,
        lease: StudioJobLease,
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_seconds)
                return
            except TimeoutError:
                pass
            try:
                async with self.session_factory() as db:
                    await set_tenant_context(db, str(lease.tenant_id))
                    renewed = await StudioRenderJobService(
                        db, tenant_id=lease.tenant_id
                    ).renew_lease(lease)
            except asyncio.CancelledError:
                raise
            except Exception:
                lease_lost.set()
                return
            if not renewed:
                lease_lost.set()
                return

    async def _progress(self, lease: StudioJobLease, progress: int) -> bool:
        async with self.session_factory() as db:
            await set_tenant_context(db, str(lease.tenant_id))
            return await StudioRenderJobService(
                db, tenant_id=lease.tenant_id
            ).update_progress(lease, progress)

    async def _await_lease_bound_phase(
        self,
        operation: Awaitable[_PhaseResult],
        lease_lost: asyncio.Event,
    ) -> _PhaseResult:
        task = asyncio.ensure_future(operation)
        lease_watch = asyncio.create_task(lease_lost.wait())
        try:
            done, _ = await asyncio.wait(
                {task, lease_watch},
                timeout=self.processor_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lease_lost.is_set():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise StudioRenderServiceError(
                    409, "cancelled", "Studio processing stopped."
                )
            if task in done:
                return task.result()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            code = "cancelled" if lease_lost.is_set() else "processor_timeout"
            raise StudioRenderServiceError(409, code, "Studio processing stopped.")
        finally:
            if not lease_watch.done():
                lease_watch.cancel()
            await asyncio.gather(lease_watch, return_exceptions=True)

    async def _load_inputs(
        self, lease: StudioJobLease
    ) -> tuple[bytes, dict, bytes | None]:
        queued = lease.payload
        async with self.session_factory() as db:
            await set_tenant_context(db, str(lease.tenant_id))
            draft = await db.scalar(
                select(StudioDraft).where(
                    StudioDraft.id == queued.draft_id,
                    StudioDraft.tenant_id == lease.tenant_id,
                )
            )
            snapshot = await db.scalar(
                select(StudioDraftSnapshot).where(
                    StudioDraftSnapshot.id == queued.snapshot_id,
                    StudioDraftSnapshot.draft_id == queued.draft_id,
                    StudioDraftSnapshot.tenant_id == lease.tenant_id,
                )
            )
            if (
                draft is None
                or draft.lifecycle_state != "active"
                or draft.cancellation_requested_at is not None
                or draft.revision != queued.rendered_revision
                or draft.identity_sha256 != queued.identity_sha256
                or draft.source_artifact_id != queued.source.artifact_id
                or draft.source_sha256 != queued.source.sha256
                or draft.source_media_type != queued.source.media_type
                or draft.format != queued.source.format
                or snapshot is None
                or snapshot.revision != queued.rendered_revision
                or snapshot.identity_sha256 != queued.identity_sha256
                or snapshot.content_sha256 != queued.snapshot_content_sha256
                or snapshot.source_artifact_id != queued.source.artifact_id
            ):
                raise StudioRenderServiceError(
                    409,
                    "validation_failed",
                    "Immutable Studio snapshot binding failed validation.",
                )
            try:
                source = await StudioSourceRegistry(
                    db, lease.tenant_id, queued.requested_by
                ).read(
                    queued.source.artifact_id,
                    expected_sha256=queued.source.sha256,
                    expected_media_type=queued.source.media_type,
                    expected_format=queued.source.format,
                )
            except StudioError as exc:
                raise StudioRenderServiceError(
                    409,
                    "source_integrity_failed",
                    "Studio source failed its integrity boundary.",
                ) from exc
            snapshot_payload = dict(snapshot.payload)

        input_binding = None
        if queued.input_binding_id is not None:
            if self.input_bindings is None:
                raise StudioRenderServiceError(
                    409,
                    "validation_failed",
                    "Studio input binding resolver is unavailable.",
                )
            ref = await self.input_bindings.resolve(
                lease.tenant_id, queued.input_binding_id
            )
            if ref.tenant_id != lease.tenant_id:
                raise StudioRenderServiceError(
                    409, "validation_failed", "Studio input binding is invalid."
                )
            input_binding = await asyncio.wait_for(
                asyncio.to_thread(
                    self.object_store.read,
                    ref,
                    max_bytes=queued.render_options.max_output_bytes,
                ),
                timeout=self.processor_timeout_seconds,
            )
        return source, snapshot_payload, input_binding

    async def _cached_output(self, lease: StudioJobLease) -> StudioObjectRef | None:
        # Opaque bindings are server-owned but are not required to be immutable
        # content addresses. Never reuse a cached render unless no binding exists.
        if lease.payload.input_binding_id is not None:
            return None
        async with self.session_factory() as db:
            await set_tenant_context(db, str(lease.tenant_id))
            cached = await StudioRenderJobService(
                db, tenant_id=lease.tenant_id
            ).find_cached_output(lease.payload.cache_key)
        if cached is None:
            return None
        queued = lease.payload
        if (
            cached.artifact_kind
            != {
                "studio_template_analysis": "analysis",
                "studio_template_ocr": "ocr",
                "studio_page_preview": "page_preview",
                "studio_test_render": "test_render",
            }[queued.kind]
            or cached.renderer_identity != queued.renderer_identity
            or cached.converter_identity != queued.converter_identity
            or cached.validator_identity != queued.validator_identity
        ):
            return None
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self.object_store.read,
                    cached.object_ref,
                    max_bytes=queued.render_options.max_output_bytes,
                ),
                timeout=self.processor_timeout_seconds,
            )
        except StudioStorageError:
            return None
        return cached.object_ref

    async def _adopt(
        self,
        lease: StudioJobLease,
        output: StudioObjectRef,
        processor_output: StudioProcessorOutput | None,
    ) -> None:
        queued = lease.payload
        artifact_kind = {
            "studio_template_analysis": "analysis",
            "studio_template_ocr": "ocr",
            "studio_page_preview": "page_preview",
            "studio_test_render": "test_render",
        }[queued.kind]
        async with self.session_factory() as db:
            await set_tenant_context(db, str(lease.tenant_id))
            await StudioRenderJobService(db, tenant_id=lease.tenant_id).adopt_output(
                lease,
                output,
                object_store=self.object_store,
                artifact_kind=artifact_kind,
                renderer_identity=(
                    processor_output.renderer_identity
                    if processor_output
                    else queued.renderer_identity
                ),
                converter_identity=(
                    processor_output.converter_identity
                    if processor_output
                    else queued.converter_identity
                ),
                validator_identity=(
                    processor_output.validator_identity
                    if processor_output
                    else queued.validator_identity
                ),
                retention_class=(
                    processor_output.retention_class if processor_output else "review"
                ),
                expires_at=datetime.now(timezone.utc)
                + timedelta(seconds=self.artifact_ttl_seconds),
            )

    async def _fail(
        self, lease: StudioJobLease, code: str, *, retryable: bool
    ) -> None:
        async with self.session_factory() as db:
            await set_tenant_context(db, str(lease.tenant_id))
            await StudioRenderJobService(
                db, tenant_id=lease.tenant_id
            ).fail_owned_job(lease, code, retryable=retryable)

    @staticmethod
    async def _terminate_processor(
        processor: StudioProcessor, processing: asyncio.Task
    ) -> None:
        try:
            await asyncio.wait_for(processor.terminate(), timeout=5)
        except Exception:
            pass
        processing.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(processing), timeout=5)
        except asyncio.CancelledError:
            pass
        except Exception:
            # The server-owned adapter is required to make terminate() kill the
            # OS process tree. Never wait without a bound for a broken adapter.
            pass

    async def process(self, job_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        tenant_id = uuid.UUID(str(tenant_id))
        async with self.session_factory() as db:
            await set_tenant_context(db, str(tenant_id))
            lease = await StudioRenderJobService(db, tenant_id=tenant_id).claim(
                job_id,
                owner=self.owner,
                lease_seconds=self.lease_seconds,
            )
        if lease is None:
            return False
        processor = self.processors.get(lease.payload.kind)
        if processor is None:
            await self._fail(lease, "processor_unavailable", retryable=False)
            return True

        stop = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(lease, stop, lease_lost))
        processing: asyncio.Task | None = None
        lease_watch: asyncio.Task | None = None
        try:
            # Recheck the mutable head and verify the immutable snapshot/source
            # before either processor execution or a cache hit is adopted.
            source, snapshot, input_binding = await self._await_lease_bound_phase(
                self._load_inputs(lease), lease_lost
            )
            cached = await self._await_lease_bound_phase(
                self._cached_output(lease), lease_lost
            )
            if cached is not None:
                if not await self._progress(lease, 90):
                    raise StudioRenderServiceError(
                        409, "cancelled", "Studio processing stopped."
                    )
                await self._adopt(lease, cached, None)
                return True
            if not await self._progress(lease, 20):
                raise StudioRenderServiceError(
                    409, "cancelled", "Studio processing stopped."
                )
            processing = asyncio.create_task(
                processor.process(
                    source=source,
                    snapshot=snapshot,
                    options=lease.payload.render_options.model_dump(mode="json"),
                    input_binding=input_binding,
                )
            )
            lease_watch = asyncio.create_task(lease_lost.wait())
            done, _ = await asyncio.wait(
                {processing, lease_watch},
                timeout=self.processor_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if processing not in done:
                await self._terminate_processor(processor, processing)
                code = "cancelled" if lease_lost.is_set() else "processor_timeout"
                raise StudioRenderServiceError(409, code, "Studio processing stopped.")
            lease_watch.cancel()
            await asyncio.gather(lease_watch, return_exceptions=True)
            result = processing.result()
            expected_artifact_kind = {
                "studio_template_analysis": "analysis",
                "studio_template_ocr": "ocr",
                "studio_page_preview": "page_preview",
                "studio_test_render": "test_render",
            }[lease.payload.kind]
            if result.artifact_kind != expected_artifact_kind:
                raise StudioRenderServiceError(
                    409,
                    "validation_failed",
                    "Studio processor artifact kind is invalid.",
                )
            if len(result.content) > lease.payload.render_options.max_output_bytes:
                raise StudioRenderServiceError(
                    413, "output_too_large", "Studio output exceeded its limit."
                )
            digest = hashlib.sha256(result.content).hexdigest()
            if digest != result.content_sha256:
                raise StudioRenderServiceError(
                    409,
                    "storage_integrity_failed",
                    "Studio output failed its integrity check.",
                )
            stored = await asyncio.wait_for(
                asyncio.to_thread(
                    self.object_store.put,
                    tenant_id,
                    result.content,
                    media_type=result.media_type,
                    expected_sha256=result.content_sha256,
                ),
                timeout=self.processor_timeout_seconds,
            )
            if not await self._progress(lease, 90):
                raise StudioRenderServiceError(
                    409, "cancelled", "Studio processing stopped."
                )
            await self._adopt(lease, stored, result)
            return True
        except StudioRenderServiceError as exc:
            await self._fail(
                lease,
                exc.code,
                retryable=exc.code in {"processor_timeout", "processor_unavailable"},
            )
            return True
        except StudioIsolationError as exc:
            code, retryable = _isolation_failure(exc.code)
            await self._fail(
                lease,
                code,
                retryable=retryable,
            )
            return True
        except StudioStorageError:
            await self._fail(lease, "storage_integrity_failed", retryable=False)
            return True
        except asyncio.CancelledError:
            if processing is not None and not processing.done():
                await self._terminate_processor(processor, processing)
            raise
        except Exception:
            await self._fail(lease, "processor_unavailable", retryable=True)
            return True
        finally:
            if lease_watch is not None and not lease_watch.done():
                lease_watch.cancel()
                await asyncio.gather(lease_watch, return_exceptions=True)
            if processing is not None and not processing.done():
                await self._terminate_processor(processor, processing)
            stop.set()
            await heartbeat
