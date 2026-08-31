"""Public facade and fenced state machine for durable Studio render jobs."""

from __future__ import annotations

import asyncio
import hashlib
import re
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from pydantic import Field, model_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.durable_job import DurableJob
from app.models.studio_draft import StudioDraft, StudioDraftSnapshot
from app.models.studio_render import StudioRenderArtifact
from app.schemas.studio_render import (
    STUDIO_RENDER_JOB_KINDS,
    STUDIO_PUBLIC_FAILURES,
    StrictModel,
    StudioRenderAccepted,
    StudioRenderJobStatus,
    StudioRenderOptions,
    StudioRenderRequest,
    StudioRenderSourceContract,
    canonical_json_sha256,
)
from app.services.studio_drafts import StudioDraftService
from app.services.studio_object_storage import StudioObjectRef, StudioObjectStore


DEFAULT_RENDERER_IDENTITY = "studio-renderer-boundary-v1"
DEFAULT_CONVERTER_IDENTITY = "studio-converter-boundary-v1"
DEFAULT_VALIDATOR_IDENTITY = "studio-validator-v1"

_ACTIVE_STATES = frozenset({"pending", "running", "cancel_requested"})
_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
_TRANSITIONS = {
    "pending": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"pending", "cancel_requested", "completed", "failed"}),
    "cancel_requested": frozenset({"cancelled", "completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_ARTIFACT_KIND_BY_JOB = {
    "studio_template_analysis": "analysis",
    "studio_template_ocr": "ocr",
    "studio_page_preview": "page_preview",
    "studio_test_render": "test_render",
}
_RETENTION_CLASSES = frozenset({"ephemeral", "review", "evidence"})
_SAFE_STATUS_BASE = re.compile(r"^/api/[A-Za-z0-9_./-]+$")


class StudioRenderServiceError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class StudioInputBindingResolver(Protocol):
    async def resolve(
        self, tenant_id: uuid.UUID, binding_id: uuid.UUID
    ) -> StudioObjectRef: ...


class _QueuedPayload(StrictModel):
    """Private, reference-only JSON stored in ``durable_jobs.payload``."""

    contract_version: int = Field(default=1, ge=1, le=1)
    kind: str
    draft_id: uuid.UUID
    rendered_revision: int = Field(ge=1)
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: uuid.UUID
    snapshot_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: StudioRenderSourceContract
    render_options: StudioRenderOptions
    render_options_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_by: uuid.UUID
    input_binding_id: uuid.UUID | None = None
    renderer_identity: str = Field(min_length=1, max_length=200)
    converter_identity: str = Field(min_length=1, max_length=200)
    validator_identity: str = Field(min_length=1, max_length=200)
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    lease_token: uuid.UUID | None = None
    lease_duration_seconds: int | None = Field(default=None, ge=30, le=3600)
    lease_expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_queue_contract(self):
        if self.kind not in STUDIO_RENDER_JOB_KINDS:
            raise ValueError("unsupported Studio durable job kind")
        if self.render_options.sha256 != self.render_options_sha256:
            raise ValueError("render options hash mismatch")
        expected_cache_key = _render_cache_key(
            kind=self.kind,
            draft_id=self.draft_id,
            rendered_revision=self.rendered_revision,
            identity_sha256=self.identity_sha256,
            snapshot_id=self.snapshot_id,
            snapshot_content_sha256=self.snapshot_content_sha256,
            source=self.source,
            render_options_sha256=self.render_options_sha256,
            input_binding_id=self.input_binding_id,
            renderer_identity=self.renderer_identity,
            converter_identity=self.converter_identity,
            validator_identity=self.validator_identity,
        )
        if self.cache_key != expected_cache_key:
            raise ValueError("Studio render cache key mismatch")
        return self


@dataclass(frozen=True)
class StudioJobLease:
    job_id: uuid.UUID
    tenant_id: uuid.UUID
    owner: str
    token: uuid.UUID
    attempt: int
    payload: _QueuedPayload


@dataclass(frozen=True)
class StudioCachedOutput:
    object_ref: StudioObjectRef
    artifact_kind: str
    renderer_identity: str
    converter_identity: str
    validator_identity: str


def sanitized_failure(code: str) -> tuple[str, str]:
    normalized = str(code or "").strip().lower()
    if normalized not in STUDIO_PUBLIC_FAILURES:
        normalized = "processor_unavailable"
    return normalized, STUDIO_PUBLIC_FAILURES[normalized]


def _idempotency_scope(key: str) -> str:
    normalized = str(key or "").strip()
    if not 8 <= len(normalized) <= 200 or any(ord(char) < 32 for char in normalized):
        raise StudioRenderServiceError(
            422,
            "invalid_idempotency_key",
            "Idempotency-Key must be 8-200 printable characters.",
        )
    return f"studio-render:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _render_cache_key(
    *,
    kind: str,
    draft_id: uuid.UUID,
    rendered_revision: int,
    identity_sha256: str,
    snapshot_id: uuid.UUID,
    snapshot_content_sha256: str,
    source: StudioRenderSourceContract,
    render_options_sha256: str,
    input_binding_id: uuid.UUID | None,
    renderer_identity: str,
    converter_identity: str,
    validator_identity: str,
) -> str:
    return canonical_json_sha256(
        {
            "kind": kind,
            "draft_id": str(draft_id),
            "rendered_revision": rendered_revision,
            "identity_sha256": identity_sha256,
            "snapshot_id": str(snapshot_id),
            "snapshot_content_sha256": snapshot_content_sha256,
            "source": source.model_dump(mode="json"),
            "render_options_sha256": render_options_sha256,
            "input_binding_id": str(input_binding_id) if input_binding_id else None,
            "renderer_identity": renderer_identity,
            "converter_identity": converter_identity,
            "validator_identity": validator_identity,
        }
    )


def _transition(row: DurableJob, target: str, *, now: datetime) -> None:
    source = str(row.status)
    if target not in _TRANSITIONS.get(source, frozenset()):
        raise StudioRenderServiceError(
            409,
            "invalid_job_transition",
            "Studio job state changed before this operation completed.",
        )
    row.status = target
    if target in _TERMINAL_STATES:
        row.progress = 100
        row.completed_at = now
        row.leased_at = None
        row.lease_owner = None


def _status_state(row: DurableJob, now: datetime) -> str:
    if row.status == "pending" and row.available_at and row.available_at > now:
        return "retry_wait"
    if row.status in _TRANSITIONS:
        return row.status
    return "failed"


class StudioRenderJobService:
    """The only Phase 4-visible access path to render jobs and artifacts."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        active_job_limit: int = 4,
        job_ttl: timedelta = timedelta(hours=24),
        renderer_identity: str = DEFAULT_RENDERER_IDENTITY,
        converter_identity: str = DEFAULT_CONVERTER_IDENTITY,
        validator_identity: str = DEFAULT_VALIDATOR_IDENTITY,
    ):
        if not 1 <= active_job_limit <= 32:
            raise ValueError("active_job_limit must be between 1 and 32")
        if not timedelta(minutes=5) <= job_ttl <= timedelta(days=7):
            raise ValueError("job_ttl must be between five minutes and seven days")
        self.db = db
        self.tenant_id = uuid.UUID(str(tenant_id))
        self.actor_user_id = (
            uuid.UUID(str(actor_user_id)) if actor_user_id is not None else None
        )
        self.active_job_limit = active_job_limit
        self.job_ttl = job_ttl
        self.renderer_identity = renderer_identity
        self.converter_identity = converter_identity
        self.validator_identity = validator_identity

    async def _validated_revision(self, request: StudioRenderRequest) -> None:
        draft = await self.db.scalar(
            select(StudioDraft).where(
                StudioDraft.id == request.draft_id,
                StudioDraft.tenant_id == self.tenant_id,
            )
        )
        snapshot = await self.db.scalar(
            select(StudioDraftSnapshot).where(
                StudioDraftSnapshot.id == request.snapshot_id,
                StudioDraftSnapshot.draft_id == request.draft_id,
                StudioDraftSnapshot.tenant_id == self.tenant_id,
            )
        )
        if draft is None or snapshot is None:
            raise StudioRenderServiceError(
                404, "revision_not_found", "Studio revision not found."
            )
        source = request.source
        current = (
            draft.lifecycle_state == "active"
            and draft.cancellation_requested_at is None
            and draft.revision == request.expected_revision == snapshot.revision
            and draft.identity_sha256
            == request.identity_sha256
            == snapshot.identity_sha256
            and snapshot.content_sha256 == request.content_sha256
            and draft.source_artifact_id
            == source.artifact_id
            == snapshot.source_artifact_id
            and draft.source_sha256 == source.sha256
            and draft.source_media_type == source.media_type
            and draft.format == source.format
        )
        if not current:
            raise StudioRenderServiceError(
                409,
                "stale_revision",
                "Studio revision or source changed before processing was queued.",
            )

    async def _expire_active_jobs(self, now: datetime) -> None:
        rows = list(
            (
                await self.db.scalars(
                    select(DurableJob)
                    .where(
                        DurableJob.tenant_id == self.tenant_id,
                        DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
                        DurableJob.status.in_(_ACTIVE_STATES),
                    )
                    .with_for_update()
                )
            ).all()
        )
        for row in rows:
            try:
                expires_at = _QueuedPayload.model_validate(row.payload).expires_at
            except Exception:
                expires_at = now
            if expires_at <= now:
                code, message = sanitized_failure("expired")
                row.last_error = message
                row.result = {"error_code": code}
                _transition(row, "failed", now=now)

    async def _enqueue_impl(
        self,
        request: StudioRenderRequest,
        *,
        idempotency_key: str,
        status_base_url: str = "/api/template-studio/render-jobs",
    ) -> StudioRenderAccepted:
        if self.actor_user_id is None or request.requested_by != self.actor_user_id:
            raise StudioRenderServiceError(
                403, "actor_mismatch", "Studio actor binding is invalid."
            )
        if not _SAFE_STATUS_BASE.fullmatch(status_base_url):
            raise StudioRenderServiceError(
                500, "invalid_status_resource", "Studio status resource is unavailable."
            )
        scope = _idempotency_scope(idempotency_key)
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"{self.tenant_id}:studio-render-admission"},
        )
        existing = await self.db.scalar(
            select(DurableJob).where(
                DurableJob.tenant_id == self.tenant_id,
                DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
                DurableJob.idempotency_key == scope,
            )
        )
        if existing is not None:
            queued = _QueuedPayload.model_validate(existing.payload)
            if queued.request_sha256 != request.request_sha256:
                await self.db.rollback()
                raise StudioRenderServiceError(
                    409,
                    "idempotency_key_mismatch",
                    "Idempotency-Key was already used for another render request.",
                )
            accepted = StudioRenderAccepted(
                job_id=existing.id,
                status_url=f"{status_base_url.rstrip('/')}/{existing.id}",
                expires_at=queued.expires_at,
            )
            # Release the tenant admission advisory lock before returning a
            # replay. Leaving this transaction open would serialize unrelated
            # render admissions on the same tenant.
            await self.db.commit()
            return accepted

        try:
            await self._validated_revision(request)
        except StudioRenderServiceError:
            await self.db.rollback()
            raise
        now = datetime.now(timezone.utc)
        await self._expire_active_jobs(now)
        active_count = await self.db.scalar(
            select(func.count())
            .select_from(DurableJob)
            .where(
                DurableJob.tenant_id == self.tenant_id,
                DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
                DurableJob.status.in_(_ACTIVE_STATES),
            )
        )
        if int(active_count or 0) >= self.active_job_limit:
            await self.db.rollback()
            raise StudioRenderServiceError(
                429,
                "studio_job_quota",
                "The tenant Studio processing limit is reached.",
            )
        source = request.source
        options_sha256 = request.render_options.sha256
        queued = _QueuedPayload(
            kind=request.kind,
            draft_id=request.draft_id,
            rendered_revision=request.expected_revision,
            identity_sha256=request.identity_sha256,
            snapshot_id=request.snapshot_id,
            snapshot_content_sha256=request.content_sha256,
            source=source,
            render_options=request.render_options,
            render_options_sha256=options_sha256,
            request_sha256=request.request_sha256,
            requested_by=request.requested_by,
            input_binding_id=request.input_binding_id,
            renderer_identity=self.renderer_identity,
            converter_identity=self.converter_identity,
            validator_identity=self.validator_identity,
            cache_key=_render_cache_key(
                kind=request.kind,
                draft_id=request.draft_id,
                rendered_revision=request.expected_revision,
                identity_sha256=request.identity_sha256,
                snapshot_id=request.snapshot_id,
                snapshot_content_sha256=request.content_sha256,
                source=source,
                render_options_sha256=options_sha256,
                input_binding_id=request.input_binding_id,
                renderer_identity=self.renderer_identity,
                converter_identity=self.converter_identity,
                validator_identity=self.validator_identity,
            ),
            expires_at=now + self.job_ttl,
        )
        row = DurableJob(
            tenant_id=self.tenant_id,
            kind=request.kind,
            idempotency_key=scope,
            payload=queued.model_dump(mode="json"),
            max_attempts=5,
        )
        self.db.add(row)
        await self.db.flush()
        accepted = StudioRenderAccepted(
            job_id=row.id,
            status_url=f"{status_base_url.rstrip('/')}/{row.id}",
            expires_at=queued.expires_at,
        )
        await self.db.commit()
        return accepted

    async def enqueue(
        self,
        request: StudioRenderRequest,
        *,
        idempotency_key: str,
        status_base_url: str = "/api/template-studio/render-jobs",
    ) -> StudioRenderAccepted:
        """Run admission with guaranteed release of its transaction-level lock."""

        try:
            return await self._enqueue_impl(
                request,
                idempotency_key=idempotency_key,
                status_base_url=status_base_url,
            )
        except BaseException:
            if self.db.in_transaction():
                await self.db.rollback()
            raise

    async def enqueue_test_render(
        self,
        request: StudioRenderRequest,
        *,
        idempotency_key: str,
        status_base_url: str = "/api/template-studio/render-jobs",
    ) -> StudioRenderAccepted:
        if request.kind != "studio_test_render":
            raise StudioRenderServiceError(
                422, "invalid_job_kind", "A test-render request is required."
            )
        return await self.enqueue(
            request,
            idempotency_key=idempotency_key,
            status_base_url=status_base_url,
        )

    async def status(self, job_id: uuid.UUID) -> StudioRenderJobStatus:
        row = await self.db.scalar(
            select(DurableJob).where(
                DurableJob.id == job_id,
                DurableJob.tenant_id == self.tenant_id,
                DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
            )
        )
        if row is None:
            await self.db.rollback()
            raise StudioRenderServiceError(404, "job_not_found", "Studio job not found.")
        queued = _QueuedPayload.model_validate(row.payload)
        now = datetime.now(timezone.utc)
        if row.status in _ACTIVE_STATES and queued.expires_at <= now:
            await self.db.rollback()
            row = await self.db.scalar(
                select(DurableJob)
                .where(
                    DurableJob.id == job_id,
                    DurableJob.tenant_id == self.tenant_id,
                    DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
                )
                .with_for_update()
            )
            if row is None:
                await self.db.rollback()
                raise StudioRenderServiceError(
                    404, "job_not_found", "Studio job not found."
                )
            queued = _QueuedPayload.model_validate(row.payload)
            now = datetime.now(timezone.utc)
            if row.status in _ACTIVE_STATES and queued.expires_at <= now:
                error_code, error_message = sanitized_failure("expired")
                row.last_error = error_message
                row.result = {"error_code": error_code}
                _transition(row, "failed", now=now)
                await self.db.commit()
        state = _status_state(row, now)
        result = row.result if isinstance(row.result, dict) else {}
        error_code = error_message = None
        if state == "failed":
            error_code, error_message = sanitized_failure(result.get("error_code", ""))
        response = StudioRenderJobStatus(
            job_id=row.id,
            kind=queued.kind,
            state=state,
            progress=max(0, min(100, int(row.progress or 0))),
            draft_id=queued.draft_id,
            rendered_revision=queued.rendered_revision,
            identity_sha256=queued.identity_sha256,
            snapshot_id=queued.snapshot_id,
            content_sha256=queued.snapshot_content_sha256,
            source=queued.source,
            request_sha256=queued.request_sha256,
            renderer_identity=queued.renderer_identity,
            converter_identity=queued.converter_identity,
            validator_identity=queued.validator_identity,
            artifact_id=result.get("artifact_id") if state == "completed" else None,
            adoption_outcome=(
                result.get("adoption_outcome") if state == "completed" else None
            ),
            error_code=error_code,
            error_message=error_message,
            expires_at=queued.expires_at,
        )
        if self.db.in_transaction():
            await self.db.rollback()
        return response

    async def request_cancel(self, job_id: uuid.UUID) -> StudioRenderJobStatus:
        now = datetime.now(timezone.utc)
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == job_id,
                DurableJob.tenant_id == self.tenant_id,
                DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
            )
            .with_for_update()
        )
        if row is None:
            await self.db.rollback()
            raise StudioRenderServiceError(404, "job_not_found", "Studio job not found.")
        if row.status == "pending":
            _transition(row, "cancelled", now=now)
        elif row.status == "running":
            _transition(row, "cancel_requested", now=now)
        await self.db.commit()
        return await self.status(job_id)

    async def claim(
        self,
        job_id: uuid.UUID,
        *,
        owner: str,
        lease_seconds: int = 900,
    ) -> StudioJobLease | None:
        if not owner or len(owner) > 200:
            raise ValueError("worker owner must be 1-200 characters")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 30 <= lease_seconds <= 3600
        ):
            raise ValueError("lease_seconds must be between 30 and 3600")
        now = datetime.now(timezone.utc)
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == job_id,
                DurableJob.tenant_id == self.tenant_id,
                DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
            )
            .with_for_update(skip_locked=True)
        )
        if row is None:
            await self.db.rollback()
            return None
        queued = _QueuedPayload.model_validate(row.payload)
        if row.status in _TERMINAL_STATES or row.status not in _ACTIVE_STATES:
            await self.db.rollback()
            return None
        if queued.expires_at <= now:
            code, message = sanitized_failure("expired")
            row.last_error = message
            row.result = {"error_code": code}
            _transition(row, "failed", now=now)
            await self.db.commit()
            return None
        stale_lease = (
            queued.lease_expires_at is None or queued.lease_expires_at <= now
        )
        if row.status == "cancel_requested":
            if stale_lease:
                _transition(row, "cancelled", now=now)
                await self.db.commit()
            else:
                await self.db.rollback()
            return None
        if row.status == "pending":
            if row.available_at > now:
                await self.db.rollback()
                return None
        elif row.status == "running":
            if not stale_lease:
                await self.db.rollback()
                return None
        else:
            await self.db.rollback()
            return None
        if row.attempts >= row.max_attempts:
            code, message = sanitized_failure("processor_unavailable")
            row.last_error = message
            row.result = {"error_code": code}
            _transition(row, "failed", now=now)
            await self.db.commit()
            return None
        if row.status == "pending":
            _transition(row, "running", now=now)
        token = uuid.uuid4()
        row.attempts += 1
        row.leased_at = now
        row.lease_owner = owner
        row.last_error = None
        row.result = None
        queued.lease_token = token
        queued.lease_duration_seconds = lease_seconds
        queued.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.payload = queued.model_dump(mode="json")
        await self.db.commit()
        return StudioJobLease(
            job_id=row.id,
            tenant_id=self.tenant_id,
            owner=owner,
            token=token,
            attempt=row.attempts,
            payload=queued,
        )

    @staticmethod
    def _owns(row: DurableJob | None, lease: StudioJobLease) -> bool:
        if (
            row is None
            or row.tenant_id != lease.tenant_id
            or row.lease_owner != lease.owner
            or row.status not in {"running", "cancel_requested"}
        ):
            return False
        try:
            queued = _QueuedPayload.model_validate(row.payload)
        except Exception:
            return False
        return queued.lease_token == lease.token and row.attempts == lease.attempt

    async def renew_lease(self, lease: StudioJobLease) -> bool:
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == lease.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if not self._owns(row, lease) or row.status != "running":
            await self.db.rollback()
            return False
        queued = _QueuedPayload.model_validate(row.payload)
        if queued.lease_duration_seconds is None:
            await self.db.rollback()
            return False
        now = datetime.now(timezone.utc)
        queued.lease_expires_at = now + timedelta(
            seconds=queued.lease_duration_seconds
        )
        row.payload = queued.model_dump(mode="json")
        row.leased_at = now
        await self.db.commit()
        return True

    async def update_progress(self, lease: StudioJobLease, progress: int) -> bool:
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == lease.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if not self._owns(row, lease) or row.status != "running":
            await self.db.rollback()
            return False
        row.progress = max(int(row.progress or 0), min(95, int(progress)))
        await self.db.commit()
        return True

    async def fail_owned_job(
        self,
        lease: StudioJobLease,
        code: str,
        *,
        retryable: bool,
    ) -> bool:
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == lease.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if not self._owns(row, lease):
            await self.db.rollback()
            return False
        now = datetime.now(timezone.utc)
        public_code, public_message = sanitized_failure(code)
        row.last_error = public_message
        row.result = {"error_code": public_code}
        if row.status == "cancel_requested" or public_code == "cancelled":
            _transition(row, "cancelled", now=now)
        elif retryable and row.attempts < row.max_attempts:
            _transition(row, "pending", now=now)
            row.available_at = now + timedelta(
                seconds=min(3600, 15 * (2 ** max(1, row.attempts)))
            )
            row.leased_at = None
            row.lease_owner = None
        else:
            _transition(row, "failed", now=now)
        await self.db.commit()
        return True

    def _new_artifact(
        self,
        *,
        row: DurableJob,
        queued: _QueuedPayload,
        output: StudioObjectRef,
        artifact_kind: str,
        adoption_outcome: str,
        retention_class: str,
        expires_at: datetime | None,
    ) -> StudioRenderArtifact:
        return StudioRenderArtifact(
            tenant_id=self.tenant_id,
            job_id=row.id,
            draft_id=queued.draft_id,
            snapshot_id=queued.snapshot_id,
            source_artifact_id=queued.source.artifact_id,
            requested_by_user_id=queued.requested_by,
            revision=queued.rendered_revision,
            identity_sha256=queued.identity_sha256,
            snapshot_content_sha256=queued.snapshot_content_sha256,
            source_sha256=queued.source.sha256,
            request_sha256=queued.request_sha256,
            cache_key=queued.cache_key,
            artifact_kind=artifact_kind,
            content_sha256=output.sha256,
            byte_size=output.byte_size,
            media_type=output.media_type,
            object_key=output.object_key,
            renderer_identity=queued.renderer_identity,
            converter_identity=queued.converter_identity,
            validator_identity=queued.validator_identity,
            adoption_outcome=adoption_outcome,
            retention_class=retention_class,
            expires_at=expires_at,
        )

    async def _materialize_terminal(
        self,
        *,
        row: DurableJob,
        queued: _QueuedPayload,
        output: StudioObjectRef,
        artifact_kind: str,
        adoption_outcome: str,
        retention_class: str,
        expires_at: datetime | None,
        now: datetime,
    ) -> StudioRenderArtifact:
        artifact = self._new_artifact(
            row=row,
            queued=queued,
            output=output,
            artifact_kind=artifact_kind,
            adoption_outcome=adoption_outcome,
            retention_class=retention_class,
            expires_at=expires_at,
        )
        self.db.add(artifact)
        await self.db.flush()
        if artifact.id is None:
            raise RuntimeError("Studio artifact ID was not materialized")
        row.result = {
            "artifact_id": str(artifact.id),
            "adoption_outcome": adoption_outcome,
        }
        _transition(row, "completed", now=now)
        return artifact

    async def _adopt_output_impl(
        self,
        lease: StudioJobLease,
        output: StudioObjectRef,
        *,
        object_store: StudioObjectStore,
        artifact_kind: str,
        renderer_identity: str,
        converter_identity: str,
        validator_identity: str,
        retention_class: str = "review",
        expires_at: datetime | None,
    ) -> tuple[uuid.UUID, str]:
        """Materialize job-owned artifact evidence with a fenced exact adoption."""

        if output.tenant_id != self.tenant_id:
            raise StudioRenderServiceError(
                409, "storage_integrity_failed", "Studio output tenant is invalid."
            )
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {
                "scope": (
                    f"{self.tenant_id}:studio-object:{output.object_key}"
                )
            },
        )
        try:
            verified = await asyncio.wait_for(
                asyncio.to_thread(
                    object_store.read, output, max_bytes=output.byte_size
                ),
                timeout=30,
            )
        except Exception as exc:
            raise StudioRenderServiceError(
                409,
                "storage_integrity_failed",
                "Studio output failed its integrity check.",
            ) from exc
        if len(verified) != output.byte_size:
            raise StudioRenderServiceError(
                409,
                "storage_integrity_failed",
                "Studio output failed its integrity check.",
            )
        if artifact_kind != _ARTIFACT_KIND_BY_JOB.get(lease.payload.kind):
            raise StudioRenderServiceError(
                409, "validation_failed", "Studio output kind is invalid."
            )
        if retention_class not in _RETENTION_CLASSES:
            raise StudioRenderServiceError(
                409, "validation_failed", "Studio retention class is invalid."
            )
        if (
            renderer_identity != lease.payload.renderer_identity
            or converter_identity != lease.payload.converter_identity
            or validator_identity != lease.payload.validator_identity
        ):
            raise StudioRenderServiceError(
                409, "validation_failed", "Studio processor attestation is invalid."
            )
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == lease.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if not self._owns(row, lease):
            raise StudioRenderServiceError(
                409, "lease_lost", "Studio job lease is no longer owned."
            )
        queued = _QueuedPayload.model_validate(row.payload)
        now = datetime.now(timezone.utc)
        if row.status == "cancel_requested":
            artifact = await self._materialize_terminal(
                row=row,
                queued=queued,
                output=output,
                artifact_kind=artifact_kind,
                adoption_outcome="cancelled_output",
                retention_class=retention_class,
                expires_at=expires_at,
                now=now,
            )
            await self.db.commit()
            return artifact.id, "cancelled_output"

        artifact = await self._materialize_terminal(
            row=row,
            queued=queued,
            output=output,
            artifact_kind=artifact_kind,
            adoption_outcome="current_evidence",
            retention_class=retention_class,
            expires_at=expires_at,
            now=now,
        )
        artifact_id = artifact.id
        current = await StudioDraftService(
            self.db, self.tenant_id, queued.requested_by
        ).mark_render_evidence_if_current(
            queued.draft_id,
            queued.rendered_revision,
            queued.identity_sha256,
        )
        if current:
            return artifact_id, "current_evidence"

        # Phase 2 intentionally rolled back the staged job/artifact mutations.
        # Rebind tenant RLS, re-lock the same lease fence, and persist stale output.
        await set_tenant_context(self.db, str(self.tenant_id))
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {
                "scope": (
                    f"{self.tenant_id}:studio-object:{output.object_key}"
                )
            },
        )
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    object_store.read, output, max_bytes=output.byte_size
                ),
                timeout=30,
            )
        except Exception as exc:
            await self.db.rollback()
            raise StudioRenderServiceError(
                409,
                "storage_integrity_failed",
                "Studio output failed its integrity check.",
            ) from exc
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == lease.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if not self._owns(row, lease):
            raise StudioRenderServiceError(
                409, "lease_lost", "Studio job lease is no longer owned."
            )
        queued = _QueuedPayload.model_validate(row.payload)
        artifact = await self._materialize_terminal(
            row=row,
            queued=queued,
            output=output,
            artifact_kind=artifact_kind,
            adoption_outcome="stale_output",
            retention_class=retention_class,
            expires_at=expires_at,
            now=datetime.now(timezone.utc),
        )
        await self.db.commit()
        return artifact.id, "stale_output"

    async def adopt_output(
        self,
        lease: StudioJobLease,
        output: StudioObjectRef,
        *,
        object_store: StudioObjectStore,
        artifact_kind: str,
        renderer_identity: str,
        converter_identity: str,
        validator_identity: str,
        retention_class: str = "review",
        expires_at: datetime | None,
    ) -> tuple[uuid.UUID, str]:
        """Adopt with guaranteed release of row and advisory locks on errors."""

        try:
            return await self._adopt_output_impl(
                lease,
                output,
                object_store=object_store,
                artifact_kind=artifact_kind,
                renderer_identity=renderer_identity,
                converter_identity=converter_identity,
                validator_identity=validator_identity,
                retention_class=retention_class,
                expires_at=expires_at,
            )
        except BaseException:
            if self.db.in_transaction():
                await self.db.rollback()
            raise

    async def find_cached_output(
        self, cache_key: str, *, now: datetime | None = None
    ) -> StudioCachedOutput | None:
        if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
            return None
        instant = now or datetime.now(timezone.utc)
        artifact = await self.db.scalar(
            select(StudioRenderArtifact)
            .where(
                StudioRenderArtifact.tenant_id == self.tenant_id,
                StudioRenderArtifact.cache_key == cache_key,
                StudioRenderArtifact.storage_state == "active",
                or_(
                    StudioRenderArtifact.expires_at.is_(None),
                    StudioRenderArtifact.expires_at > instant,
                ),
            )
            .order_by(StudioRenderArtifact.created_at.desc())
            .limit(1)
        )
        if artifact is None:
            return None
        return StudioCachedOutput(
            object_ref=StudioObjectRef(
                tenant_id=artifact.tenant_id,
                object_key=artifact.object_key,
                sha256=artifact.content_sha256,
                byte_size=artifact.byte_size,
                media_type=artifact.media_type,
            ),
            artifact_kind=artifact.artifact_kind,
            renderer_identity=artifact.renderer_identity,
            converter_identity=artifact.converter_identity,
            validator_identity=artifact.validator_identity,
        )


def default_worker_owner() -> str:
    return f"studio-render:{socket.gethostname()}:{uuid.uuid4()}"
