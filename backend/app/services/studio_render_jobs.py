"""Public facade and fenced state machine for durable Studio render jobs."""

from __future__ import annotations

import asyncio
import hashlib
import re
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Awaitable, Callable, Mapping, Protocol, TypeVar

from pydantic import AwareDatetime, Field, ValidationError, model_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.durable_job import DurableJob
from app.models.studio_draft import (
    StudioDraft,
    StudioDraftAuditEvent,
    StudioDraftSnapshot,
    StudioSourceArtifact,
)
from app.models.studio_render import (
    StudioPreferredRenderEvidence,
    StudioRenderArtifact,
)
from app.models.tenant import Tenant
from app.schemas.studio_render import (
    STUDIO_RENDER_JOB_KINDS,
    STUDIO_PUBLIC_ERROR_DETAIL_KEYS,
    STUDIO_PUBLIC_ERROR_MESSAGES,
    STUDIO_PUBLIC_ERROR_RETRYABLE,
    STUDIO_PUBLIC_ERROR_STATUS,
    STUDIO_PUBLIC_FAILURES,
    StrictModel,
    StudioArtifactGeometry,
    StudioGeometryManifest,
    StudioRenderAccepted,
    StudioRenderErrorDetails,
    StudioRenderJobStatus,
    StudioRenderOptions,
    StudioRenderPublicError,
    StudioRenderRequest,
    StudioRendererManifest,
    StudioRenderSourceContract,
    canonical_effective_render_request_hash,
    canonical_render_request_hash,
    canonical_json_sha256,
)
from app.services.studio_drafts import StudioDraftService
from app.services.studio_object_storage import (
    StudioObjectRef,
    StudioObjectStore,
    StudioStagedObject,
    run_storage_mutation_to_completion,
    run_storage_operation_to_completion,
)


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
_MEDIA_TYPES_BY_ARTIFACT_KIND = {
    "analysis": frozenset({"application/json"}),
    "ocr": frozenset({"application/json"}),
    "page_preview": frozenset({"image/png"}),
    "test_render": frozenset(
        {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    ),
}
_RETENTION_CLASSES = frozenset({"ephemeral", "review", "evidence"})
_PREFERRED_EVIDENCE_JOB_KINDS = frozenset({"studio_test_render"})
_CANONICAL_STATUS_BASE = "/api/template-studio/render-jobs"
_STORAGE_STAGE_TIMEOUT_SECONDS = 30.0
_STORAGE_READ_TIMEOUT_SECONDS = 30.0
_ConsumerResult = TypeVar("_ConsumerResult")


def _geometry_matches_request(
    manifest: StudioGeometryManifest,
    *,
    artifact_kind: str,
    requested_page_number: int | None,
) -> bool:
    page_numbers = [page.page_number for page in manifest.pages]
    if artifact_kind == "page_preview":
        return (
            requested_page_number is not None
            and manifest.artifact_page_count == 1
            and page_numbers == [requested_page_number]
            and requested_page_number <= manifest.document_page_count
        )
    return (
        requested_page_number is None
        and manifest.artifact_page_count == manifest.document_page_count
        and page_numbers == list(range(1, manifest.document_page_count + 1))
    )


class StudioRenderServiceError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        durable_state_changed: bool = False,
    ):
        normalized = str(code or "").strip().lower()
        if normalized not in STUDIO_PUBLIC_ERROR_MESSAGES:
            normalized = "processor_unavailable"
        allowed = STUDIO_PUBLIC_ERROR_DETAIL_KEYS.get(normalized, frozenset())
        candidate_details = {
            key: value for key, value in dict(details or {}).items() if key in allowed
        }
        try:
            public_details = (
                StudioRenderErrorDetails.model_validate(candidate_details)
                if candidate_details
                else None
            )
        except (ValidationError, TypeError, ValueError):
            public_details = None
        canonical_message = STUDIO_PUBLIC_ERROR_MESSAGES[normalized]
        super().__init__(canonical_message)
        self.status_code = STUDIO_PUBLIC_ERROR_STATUS[normalized]
        self.code = normalized
        self.message = canonical_message
        self.retryable = STUDIO_PUBLIC_ERROR_RETRYABLE[normalized]
        self.details = (
            public_details.model_dump(exclude_none=True)
            if public_details is not None
            else {}
        )
        self.durable_state_changed = durable_state_changed

    def to_public_error(self) -> StudioRenderPublicError:
        return StudioRenderPublicError(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            details=self.details or None,
        )


def studio_render_public_error(error: BaseException) -> StudioRenderPublicError:
    """Convert route-bound failures without exposing arbitrary exception text."""

    if isinstance(error, StudioRenderServiceError):
        return error.to_public_error()
    return StudioRenderServiceError(
        503,
        "processor_unavailable",
        "",
    ).to_public_error()


async def run_studio_consumer_transaction(
    db: AsyncSession,
    operation: Callable[[], Awaitable[_ConsumerResult]],
) -> _ConsumerResult:
    """Commit one consumer/audit unit and preserve sanitized poison terminalization."""

    try:
        result = await operation()
    except StudioRenderServiceError as exc:
        if exc.durable_state_changed:
            try:
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        else:
            await db.rollback()
        raise
    except BaseException:
        await db.rollback()
        raise
    try:
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return result


async def append_studio_render_audit_event(
    db: AsyncSession,
    event: str,
    job_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> None:
    """Append one redacted render event inside the caller-owned transaction."""

    if event not in {"studio_render_enqueued", "studio_render_cancel_requested"}:
        raise StudioRenderServiceError(
            503, "audit_unavailable", "Studio auditing is temporarily unavailable."
        )
    tenant_id = uuid.UUID(str(tenant_id))
    actor_user_id = uuid.UUID(str(actor_user_id))
    await set_tenant_context(db, str(tenant_id))
    row = await db.scalar(
        select(DurableJob).where(
            DurableJob.id == uuid.UUID(str(job_id)),
            DurableJob.tenant_id == tenant_id,
            DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
        )
    )
    try:
        queued = _QueuedPayload.model_validate(row.payload if row is not None else None)
    except (ValidationError, TypeError, ValueError) as exc:
        raise StudioRenderServiceError(
            503, "audit_unavailable", "Studio auditing is temporarily unavailable."
        ) from exc
    if queued.requested_by != actor_user_id:
        raise StudioRenderServiceError(
            503, "audit_unavailable", "Studio auditing is temporarily unavailable."
        )
    db.add(
        StudioDraftAuditEvent(
            tenant_id=tenant_id,
            draft_id=queued.draft_id,
            event_type=event,
            revision=queued.rendered_revision,
            actor_user_id=actor_user_id,
            detail={"job_id": str(row.id)},
        )
    )
    await db.flush()


class StudioConsumerAudit(Protocol):
    async def __call__(self, event: str, job_id: uuid.UUID) -> None: ...


class StudioInputBindingResolver(Protocol):
    async def resolve(
        self, tenant_id: uuid.UUID, binding_id: uuid.UUID
    ) -> "StudioResolvedInputBinding": ...


@dataclass(frozen=True)
class StudioResolvedInputBinding:
    object_ref: StudioObjectRef
    version: int

    def __post_init__(self) -> None:
        if not 1 <= self.version <= 2_147_483_647:
            raise ValueError("invalid Studio input binding version")


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
    effective_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_by: uuid.UUID
    input_binding_id: uuid.UUID | None = None
    input_binding_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_binding_version: int | None = Field(default=None, ge=1)
    renderer_manifest: StudioRendererManifest
    runtime_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    admission_bytes: int = Field(ge=1, le=200 * 1024 * 1024)
    expires_at: AwareDatetime
    lease_token: uuid.UUID | None = None
    lease_duration_seconds: int | None = Field(default=None, ge=30, le=3600)
    lease_expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_queue_contract(self):
        if self.kind not in STUDIO_RENDER_JOB_KINDS:
            raise ValueError("unsupported Studio durable job kind")
        if self.render_options.sha256 != self.render_options_sha256:
            raise ValueError("render options hash mismatch")
        if self.renderer_manifest.sha256 != self.runtime_manifest_sha256:
            raise ValueError("renderer manifest hash mismatch")
        if (self.input_binding_id is not None) != (
            self.input_binding_sha256 is not None
            and self.input_binding_version is not None
        ):
            raise ValueError("input binding identity is incomplete")
        if (self.lease_token is not None) != (
            self.lease_duration_seconds is not None
            and self.lease_expires_at is not None
        ):
            raise ValueError("lease fence is incomplete")
        expected_request_sha256 = canonical_render_request_hash(
            kind=self.kind,
            draft_id=self.draft_id,
            expected_revision=self.rendered_revision,
            identity_sha256=self.identity_sha256,
            snapshot_id=self.snapshot_id,
            content_sha256=self.snapshot_content_sha256,
            source=self.source,
            render_options=self.render_options,
            requested_by=self.requested_by,
            input_binding_id=self.input_binding_id,
        )
        if self.request_sha256 != expected_request_sha256:
            raise ValueError("Studio render request hash mismatch")
        expected_effective_request_sha256 = canonical_effective_render_request_hash(
            request_sha256=self.request_sha256,
            input_binding_sha256=self.input_binding_sha256,
            input_binding_version=self.input_binding_version,
        )
        if self.effective_request_sha256 != expected_effective_request_sha256:
            raise ValueError("Studio effective request hash mismatch")
        expected_cache_key = _render_cache_key(
            kind=self.kind,
            draft_id=self.draft_id,
            rendered_revision=self.rendered_revision,
            identity_sha256=self.identity_sha256,
            snapshot_id=self.snapshot_id,
            snapshot_content_sha256=self.snapshot_content_sha256,
            source=self.source,
            render_options_sha256=self.render_options_sha256,
            effective_request_sha256=self.effective_request_sha256,
            input_binding_id=self.input_binding_id,
            input_binding_sha256=self.input_binding_sha256,
            input_binding_version=self.input_binding_version,
            runtime_manifest_sha256=self.runtime_manifest_sha256,
        )
        if self.cache_key != expected_cache_key:
            raise ValueError("Studio render cache key mismatch")
        return self


class _PersistedResult(StrictModel):
    """Strict sanitized result JSON stored in ``durable_jobs.result``."""

    error_code: str | None = None
    artifact_id: uuid.UUID | None = None
    adoption_outcome: str | None = None
    preferred_evidence_at_completion: bool | None = None
    content_expires_at: AwareDatetime | None = None
    metadata_expires_at: AwareDatetime | None = None
    retention_class: str | None = None
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    media_type: str | None = Field(default=None, min_length=1, max_length=100)
    byte_size: int | None = Field(default=None, ge=1, le=100 * 1024 * 1024)
    artifact_page_count: int | None = Field(default=None, ge=1, le=1_000)
    document_page_count: int | None = Field(default=None, ge=1, le=10_000)
    geometry_manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    input_binding_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    input_binding_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.error_code is not None:
            if self.error_code not in STUDIO_PUBLIC_FAILURES:
                raise ValueError("invalid public failure code")
            if any(
                value is not None
                for value in (
                    self.artifact_id,
                    self.adoption_outcome,
                    self.preferred_evidence_at_completion,
                    self.content_expires_at,
                    self.metadata_expires_at,
                    self.retention_class,
                    self.output_sha256,
                    self.media_type,
                    self.byte_size,
                    self.artifact_page_count,
                    self.document_page_count,
                    self.geometry_manifest_sha256,
                    self.input_binding_sha256,
                    self.input_binding_version,
                )
            ):
                raise ValueError("failure result cannot contain artifact metadata")
            return self
        materialized = (
            self.artifact_id,
            self.adoption_outcome,
            self.preferred_evidence_at_completion,
            self.retention_class,
            self.output_sha256,
            self.media_type,
            self.byte_size,
            self.artifact_page_count,
            self.document_page_count,
            self.geometry_manifest_sha256,
        )
        if any(value is not None for value in materialized) and not all(
            value is not None for value in materialized
        ):
            raise ValueError("artifact result is incomplete")
        if (
            self.content_expires_at is not None or self.metadata_expires_at is not None
        ) and self.artifact_id is None:
            raise ValueError("artifact expiry has no artifact")
        if self.artifact_id is None and any(
            value is not None
            for value in (
                self.input_binding_sha256,
                self.input_binding_version,
            )
        ):
            raise ValueError("artifact metadata has no artifact")
        if (self.input_binding_sha256 is None) != (self.input_binding_version is None):
            raise ValueError("input binding identity is incomplete")
        if self.adoption_outcome is not None and self.adoption_outcome not in {
            "current_evidence",
            "stale_output",
            "cancelled_output",
        }:
            raise ValueError("invalid adoption outcome")
        if (
            self.preferred_evidence_at_completion
            and self.adoption_outcome != "current_evidence"
        ):
            raise ValueError("only current output can be preferred evidence")
        if (
            self.retention_class is not None
            and self.retention_class not in _RETENTION_CLASSES
        ):
            raise ValueError("invalid retention class")
        if self.retention_class in {"ephemeral", "review"} and (
            self.content_expires_at is None
            or self.metadata_expires_at is None
            or self.metadata_expires_at <= self.content_expires_at
        ):
            raise ValueError("temporary artifact retention expiry is required")
        if self.retention_class == "evidence" and (
            self.content_expires_at is not None or self.metadata_expires_at is not None
        ):
            raise ValueError("evidence artifact cannot expire")
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
    effective_request_sha256: str
    runtime_manifest_sha256: str
    artifact_page_count: int
    document_page_count: int
    geometry_manifest: StudioGeometryManifest
    geometry_manifest_sha256: str


@dataclass(frozen=True)
class StudioRenderArtifactContent:
    """Verified bytes returned only by the authenticated consumer facade."""

    artifact_id: uuid.UUID
    content: bytes
    sha256: str
    media_type: str

    def __post_init__(self) -> None:
        if not self.content or hashlib.sha256(self.content).hexdigest() != self.sha256:
            raise ValueError("Studio artifact content failed verification")


@dataclass(frozen=True)
class _AuthoritativeInputState:
    """Classify mutable draft movement separately from immutable corruption."""

    disposition: str
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.disposition not in {"current", "stale", "cancelled", "corrupt"}:
            raise ValueError("invalid authoritative input disposition")
        if (self.disposition == "corrupt") != (self.failure_code is not None):
            raise ValueError("invalid authoritative input failure state")


def sanitized_failure(code: str) -> tuple[str, str]:
    normalized = str(code or "").strip().lower()
    if normalized not in STUDIO_PUBLIC_FAILURES:
        normalized = "processor_unavailable"
    return normalized, STUDIO_PUBLIC_FAILURES[normalized]


def _terminalize_poison(row: DurableJob, *, now: datetime) -> None:
    """Fail closed on corrupted persisted JSON without exposing validator text."""

    code, message = sanitized_failure("job_data_unavailable")
    row.status = "failed"
    row.progress = 100
    row.last_error = message
    row.result = {"error_code": code}
    row.completed_at = now
    row.updated_at = now
    row.leased_at = None
    row.lease_owner = None


def _parse_queued(row: DurableJob, *, now: datetime) -> _QueuedPayload | None:
    try:
        return _QueuedPayload.model_validate(row.payload)
    except (ValidationError, TypeError, ValueError):
        # A malformed payload may contain values that were never valid durable
        # metadata. Scrub it while preserving only the sanitized terminal result.
        row.payload = {}
        _terminalize_poison(row, now=now)
        return None


def _parse_result(row: DurableJob, *, now: datetime) -> _PersistedResult | None:
    try:
        return _PersistedResult.model_validate(row.result or {})
    except (ValidationError, TypeError, ValueError):
        _terminalize_poison(row, now=now)
        return None


def _peek_requested_by(row: DurableJob) -> uuid.UUID | None:
    """Read only the ownership fence without validating or mutating job JSON."""

    if not isinstance(row.payload, dict):
        return None
    try:
        return uuid.UUID(str(row.payload.get("requested_by")))
    except (AttributeError, TypeError, ValueError):
        return None


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
    effective_request_sha256: str,
    input_binding_id: uuid.UUID | None,
    input_binding_sha256: str | None,
    input_binding_version: int | None,
    runtime_manifest_sha256: str,
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
            "effective_request_sha256": effective_request_sha256,
            "input_binding_id": str(input_binding_id) if input_binding_id else None,
            "input_binding_sha256": input_binding_sha256,
            "input_binding_version": input_binding_version,
            "runtime_manifest_sha256": runtime_manifest_sha256,
        }
    )


def _evidence_slot(queued: _QueuedPayload) -> str:
    """Return a bounded server-owned semantic role, never client quota knobs."""

    if queued.kind == "studio_page_preview":
        return f"page_preview:page:{queued.render_options.page_number}"
    return {
        "studio_template_analysis": "analysis:canonical",
        "studio_template_ocr": "ocr:canonical",
        "studio_test_render": "test_render:canonical",
    }[queued.kind]


def _evidence_basis_sha256(queued: _QueuedPayload) -> str:
    return canonical_json_sha256(
        {
            "contract_version": 2,
            "server_owned_evidence_slot": _evidence_slot(queued),
        }
    )


def _can_become_preferred_evidence(queued: _QueuedPayload) -> bool:
    return queued.kind in _PREFERRED_EVIDENCE_JOB_KINDS


def _transition(row: DurableJob, target: str, *, now: datetime) -> None:
    source = str(row.status)
    if target not in _TRANSITIONS.get(source, frozenset()):
        raise StudioRenderServiceError(
            409,
            "invalid_job_transition",
            "Studio job state changed before this operation completed.",
        )
    row.status = target
    row.updated_at = now
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


def _is_aware_database_time(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _snapshot_payload_is_exact(
    payload: Any,
    *,
    draft_id: uuid.UUID,
    revision: int,
    identity_sha256: str,
    content_sha256: str,
    source: StudioRenderSourceContract,
) -> bool:
    """Cryptographically re-bind immutable Phase 2 payload and source identity."""

    if not isinstance(payload, dict):
        return False
    expected_source = source.model_dump(mode="json")
    return bool(
        canonical_json_sha256(payload) == content_sha256
        and payload.get("contract_version") == 1
        and payload.get("draft_id") == str(draft_id)
        and payload.get("revision") == revision
        and payload.get("identity_sha256") == identity_sha256
        and payload.get("format") == source.format
        and payload.get("lifecycle_state") == "active"
        and payload.get("source") == expected_source
        and isinstance(payload.get("fields"), list)
        and isinstance(payload.get("placements"), list)
    )


class _StudioRenderJobStore:
    """Internal persistence state machine hidden behind narrow facades."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        active_job_limit: int = 4,
        job_ttl: timedelta = timedelta(hours=24),
        renderer_manifest: StudioRendererManifest | None = None,
        renderer_manifests: Mapping[object, StudioRendererManifest] | None = None,
        input_binding_resolver: StudioInputBindingResolver | None = None,
        enqueue_rate_limit: int = 20,
        enqueue_rate_window: timedelta = timedelta(minutes=1),
        queued_byte_limit: int = 500 * 1024 * 1024,
        max_input_binding_bytes: int = 25 * 1024 * 1024,
        max_download_bytes: int = 25 * 1024 * 1024,
        retained_artifact_limit: int = 500,
        retained_byte_limit: int = 10 * 1024**3,
        live_artifact_limit: int = 1_000,
        live_byte_limit: int = 20 * 1024**3,
    ):
        if not 1 <= active_job_limit <= 32:
            raise ValueError("active_job_limit must be between 1 and 32")
        if not timedelta(minutes=5) <= job_ttl <= timedelta(days=7):
            raise ValueError("job_ttl must be between five minutes and seven days")
        if not 1 <= enqueue_rate_limit <= 10_000:
            raise ValueError("enqueue_rate_limit must be between 1 and 10000")
        if not timedelta(seconds=1) <= enqueue_rate_window <= timedelta(hours=1):
            raise ValueError(
                "enqueue_rate_window must be between one second and one hour"
            )
        if not 1 <= queued_byte_limit <= 100 * 1024**3:
            raise ValueError("queued_byte_limit is invalid")
        if not 1 <= max_input_binding_bytes <= 100 * 1024 * 1024:
            raise ValueError("max_input_binding_bytes is invalid")
        if not 1 <= max_download_bytes <= 100 * 1024 * 1024:
            raise ValueError("max_download_bytes is invalid")
        if not 1 <= retained_artifact_limit <= 100_000:
            raise ValueError("retained_artifact_limit is invalid")
        if not 1 <= retained_byte_limit <= 10 * 1024**4:
            raise ValueError("retained_byte_limit is invalid")
        if not retained_artifact_limit <= live_artifact_limit <= 100_000:
            raise ValueError("live_artifact_limit is invalid")
        if not retained_byte_limit <= live_byte_limit <= 10 * 1024**4:
            raise ValueError("live_byte_limit is invalid")
        self.db = db
        self.tenant_id = uuid.UUID(str(tenant_id))
        self.actor_user_id = (
            uuid.UUID(str(actor_user_id)) if actor_user_id is not None else None
        )
        self.active_job_limit = active_job_limit
        self.job_ttl = job_ttl
        if renderer_manifest is not None and renderer_manifests is not None:
            raise ValueError("renderer manifest configuration is ambiguous")
        if renderer_manifest is not None:
            self.renderer_manifests = {
                (kind, source_format, 1): renderer_manifest
                for kind in STUDIO_RENDER_JOB_KINDS
                for source_format in ("markdown", "docx", "pdf")
            }
        else:
            self.renderer_manifests = {}
            for key, manifest in dict(renderer_manifests or {}).items():
                if isinstance(key, tuple) and len(key) == 3:
                    kind, source_format, contract_version = key
                    if (
                        kind in STUDIO_RENDER_JOB_KINDS
                        and source_format in {"markdown", "docx", "pdf"}
                        and contract_version == 1
                    ):
                        self.renderer_manifests[key] = manifest
                elif key in STUDIO_RENDER_JOB_KINDS:
                    for source_format in ("markdown", "docx", "pdf"):
                        self.renderer_manifests[(key, source_format, 1)] = manifest
        self.input_binding_resolver = input_binding_resolver
        self.enqueue_rate_limit = enqueue_rate_limit
        self.enqueue_rate_window = enqueue_rate_window
        self.queued_byte_limit = queued_byte_limit
        self.max_input_binding_bytes = max_input_binding_bytes
        self.max_download_bytes = max_download_bytes
        self.retained_artifact_limit = retained_artifact_limit
        self.retained_byte_limit = retained_byte_limit
        self.live_artifact_limit = live_artifact_limit
        self.live_byte_limit = live_byte_limit

    async def _bind_tenant_context(self) -> None:
        """Rebind transaction-local RLS state at every persistence boundary."""

        await set_tenant_context(self.db, str(self.tenant_id))

    async def _acquire_tenant_purge_fence(self) -> bool:
        """Serialize worker writes with demo purge and verify tenant liveness."""

        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"lawhand:demo-purge:v1:{self.tenant_id}"},
        )
        active = await self.db.scalar(
            select(Tenant.is_active).where(Tenant.id == self.tenant_id)
        )
        return active is True

    async def _clock_now(self) -> datetime:
        """Read wall-clock database time even inside an older transaction."""

        now = await self.db.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise StudioRenderServiceError(
                503,
                "processor_unavailable",
                "Studio processing is temporarily unavailable.",
            )
        return now

    async def _validated_revision(self, request: StudioRenderRequest) -> int:
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
        source_artifact = await self.db.scalar(
            select(StudioSourceArtifact).where(
                StudioSourceArtifact.id == request.source.artifact_id,
                StudioSourceArtifact.tenant_id == self.tenant_id,
            )
        )
        if draft is None or snapshot is None or source_artifact is None:
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
            and _snapshot_payload_is_exact(
                snapshot.payload,
                draft_id=request.draft_id,
                revision=request.expected_revision,
                identity_sha256=request.identity_sha256,
                content_sha256=request.content_sha256,
                source=source,
            )
            and draft.source_artifact_id
            == source.artifact_id
            == snapshot.source_artifact_id
            and draft.source_sha256 == source.sha256
            and draft.source_media_type == source.media_type
            and draft.format == source.format
            and source_artifact.sha256 == source.sha256
            and source_artifact.media_type == source.media_type
            and source_artifact.format == source.format
            and source_artifact.byte_size == len(source_artifact.content_bytes)
            and hashlib.sha256(source_artifact.content_bytes).hexdigest()
            == source.sha256
        )
        if not current:
            details = {}
            if draft is not None:
                revision = max(1, int(draft.revision or 1))
                identity = str(draft.identity_sha256 or "")
                if re.fullmatch(r"[0-9a-f]{64}", identity):
                    details = {
                        "current_revision": revision,
                        "current_etag": f'"studio:{draft.id}:{revision}:{identity}"',
                    }
            raise StudioRenderServiceError(
                409,
                "stale_revision",
                "Studio revision or source changed before processing was queued.",
                details=details,
            )
        return int(source_artifact.byte_size)

    async def _authoritative_inputs_state(
        self, queued: _QueuedPayload
    ) -> _AuthoritativeInputState:
        """Lock immutable inputs, then classify only draft-head movement as stale."""

        draft = await self.db.scalar(
            select(StudioDraft)
            .where(
                StudioDraft.id == queued.draft_id,
                StudioDraft.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        snapshot = await self.db.scalar(
            select(StudioDraftSnapshot)
            .where(
                StudioDraftSnapshot.id == queued.snapshot_id,
                StudioDraftSnapshot.draft_id == queued.draft_id,
                StudioDraftSnapshot.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        source = await self.db.scalar(
            select(StudioSourceArtifact)
            .where(
                StudioSourceArtifact.id == queued.source.artifact_id,
                StudioSourceArtifact.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if draft is None or snapshot is None:
            return _AuthoritativeInputState("corrupt", "validation_failed")
        if source is None:
            return _AuthoritativeInputState("corrupt", "source_integrity_failed")
        immutable_snapshot_current = bool(
            snapshot.revision == queued.rendered_revision
            and snapshot.identity_sha256 == queued.identity_sha256
            and snapshot.content_sha256 == queued.snapshot_content_sha256
            and snapshot.source_artifact_id == queued.source.artifact_id
            and _snapshot_payload_is_exact(
                snapshot.payload,
                draft_id=queued.draft_id,
                revision=queued.rendered_revision,
                identity_sha256=queued.identity_sha256,
                content_sha256=queued.snapshot_content_sha256,
                source=queued.source,
            )
        )
        if not immutable_snapshot_current:
            return _AuthoritativeInputState("corrupt", "validation_failed")
        immutable_source_current = bool(
            source.sha256 == queued.source.sha256
            and source.media_type == queued.source.media_type
            and source.format == queued.source.format
            and isinstance(source.content_bytes, bytes)
            and source.byte_size == len(source.content_bytes)
            and hashlib.sha256(source.content_bytes).hexdigest() == queued.source.sha256
        )
        if not immutable_source_current:
            return _AuthoritativeInputState("corrupt", "source_integrity_failed")
        if queued.input_binding_id is not None:
            if self.input_binding_resolver is None:
                return _AuthoritativeInputState("corrupt", "validation_failed")
            try:
                resolved = await asyncio.wait_for(
                    self.input_binding_resolver.resolve(
                        self.tenant_id, queued.input_binding_id
                    ),
                    timeout=5,
                )
            except Exception:
                return _AuthoritativeInputState("corrupt", "validation_failed")
            try:
                binding_current = bool(
                    resolved.object_ref.tenant_id == self.tenant_id
                    and resolved.object_ref.sha256 == queued.input_binding_sha256
                    and resolved.version == queued.input_binding_version
                )
            except Exception:
                binding_current = False
            if not binding_current:
                return _AuthoritativeInputState("corrupt", "validation_failed")
        if draft.cancellation_requested_at is not None:
            return _AuthoritativeInputState("cancelled")
        draft_head_current = bool(
            draft.lifecycle_state == "active"
            and draft.cancellation_requested_at is None
            and draft.revision == queued.rendered_revision
            and draft.identity_sha256 == queued.identity_sha256
            and draft.source_artifact_id == queued.source.artifact_id
            and draft.source_sha256 == queued.source.sha256
            and draft.source_media_type == queued.source.media_type
            and draft.format == queued.source.format
        )
        return _AuthoritativeInputState("current" if draft_head_current else "stale")

    async def _fail_corrupt_adoption(
        self,
        row: DurableJob,
        state: _AuthoritativeInputState,
        *,
        now: datetime,
    ) -> None:
        """Persist a sanitized terminal failure without materializing an artifact."""

        code, message = sanitized_failure(state.failure_code or "validation_failed")
        row.last_error = message
        row.result = {"error_code": code}
        _transition(row, "failed", now=now)
        await self.db.commit()
        raise StudioRenderServiceError(409, code, message)

    async def _expire_active_jobs(self) -> None:
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
        now = await self._clock_now()
        poisoned = False
        for row in rows:
            queued = _parse_queued(row, now=now)
            if queued is None:
                poisoned = True
                continue
            expires_at = queued.expires_at
            if expires_at <= now:
                if row.status == "cancel_requested":
                    _transition(row, "cancelled", now=now)
                    continue
                code, message = sanitized_failure("expired")
                row.last_error = message
                row.result = {"error_code": code}
                _transition(row, "failed", now=now)
        if poisoned:
            await self.db.flush()
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
                durable_state_changed=True,
            )

    async def _enqueue_impl(
        self,
        request: StudioRenderRequest,
        *,
        idempotency_key: str,
        status_base_url: str = "/api/template-studio/render-jobs",
        audit: StudioConsumerAudit | None = None,
    ) -> StudioRenderAccepted:
        if self.actor_user_id is None or request.requested_by != self.actor_user_id:
            raise StudioRenderServiceError(
                403, "actor_mismatch", "Studio actor binding is invalid."
            )
        if status_base_url != _CANONICAL_STATUS_BASE:
            raise StudioRenderServiceError(
                500, "invalid_status_resource", "Studio status resource is unavailable."
            )
        renderer_manifest = self.renderer_manifests.get(
            (
                request.kind,
                request.source.format,
                request.render_options.contract_version,
            )
        )
        if renderer_manifest is None:
            raise StudioRenderServiceError(
                503,
                "processor_unavailable",
                "Studio processing is temporarily unavailable.",
            )
        if request.render_options.max_output_bytes > self.max_download_bytes:
            raise StudioRenderServiceError(
                413,
                "output_too_large",
                "Studio output exceeds its size limit.",
            )
        await self._bind_tenant_context()
        scope = _idempotency_scope(idempotency_key)
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"{self.tenant_id}:studio-render-admission"},
        )
        now = await self._clock_now()
        existing = await self.db.scalar(
            select(DurableJob).where(
                DurableJob.tenant_id == self.tenant_id,
                DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
                DurableJob.idempotency_key == scope,
            )
        )
        if existing is not None:
            queued = _parse_queued(existing, now=now)
            if queued is None:
                await self.db.flush()
                raise StudioRenderServiceError(
                    409,
                    "job_data_unavailable",
                    STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
                    durable_state_changed=True,
                )
            if queued.request_sha256 != request.request_sha256:
                raise StudioRenderServiceError(
                    409,
                    "idempotency_key_mismatch",
                    "Idempotency-Key was already used for another render request.",
                )
            if queued.expires_at <= now:
                raise StudioRenderServiceError(
                    409,
                    "idempotency_key_expired",
                    "Idempotency-Key refers to an expired Studio request.",
                )
            accepted = StudioRenderAccepted(
                job_id=existing.id,
                status_url=f"{status_base_url.rstrip('/')}/{existing.id}",
                job_expires_at=queued.expires_at,
            )
            return accepted

        source_bytes = await self._validated_revision(request)
        binding_sha256: str | None = None
        binding_version: int | None = None
        binding_bytes = 0
        if request.input_binding_id is not None:
            if self.input_binding_resolver is None:
                raise StudioRenderServiceError(
                    409,
                    "validation_failed",
                    "Studio input binding is unavailable.",
                )
            try:
                resolved = await asyncio.wait_for(
                    self.input_binding_resolver.resolve(
                        self.tenant_id, request.input_binding_id
                    ),
                    timeout=5,
                )
            except Exception as exc:
                raise StudioRenderServiceError(
                    409,
                    "validation_failed",
                    "Studio input binding is unavailable.",
                ) from exc
            if resolved.object_ref.tenant_id != self.tenant_id:
                raise StudioRenderServiceError(
                    409, "validation_failed", "Studio input binding is invalid."
                )
            binding_sha256 = resolved.object_ref.sha256
            binding_version = resolved.version
            binding_bytes = resolved.object_ref.byte_size
            if binding_bytes > self.max_input_binding_bytes:
                raise StudioRenderServiceError(
                    413,
                    "input_too_large",
                    STUDIO_PUBLIC_FAILURES["input_too_large"],
                )
        # Resolver I/O may span an expiry/rate boundary. Lock and expire active
        # jobs first, then timestamp admission after every possible lock wait.
        await self._expire_active_jobs()
        now = await self._clock_now()
        admission_bytes = (
            source_bytes + binding_bytes + request.render_options.max_output_bytes
        )
        if admission_bytes > 200 * 1024 * 1024:
            raise StudioRenderServiceError(
                413,
                "input_too_large",
                STUDIO_PUBLIC_FAILURES["input_too_large"],
            )
        rate_count = await self.db.scalar(
            select(func.count())
            .select_from(DurableJob)
            .where(
                DurableJob.tenant_id == self.tenant_id,
                DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
                DurableJob.created_at >= now - self.enqueue_rate_window,
            )
        )
        if int(rate_count or 0) >= self.enqueue_rate_limit:
            raise StudioRenderServiceError(
                429,
                "studio_job_rate",
                "The tenant Studio submission rate is reached.",
            )
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
            raise StudioRenderServiceError(
                429,
                "studio_job_quota",
                "The tenant Studio processing limit is reached.",
            )
        active_rows = list(
            (
                await self.db.scalars(
                    select(DurableJob).where(
                        DurableJob.tenant_id == self.tenant_id,
                        DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
                        DurableJob.status.in_(_ACTIVE_STATES),
                    )
                )
            ).all()
        )
        queued_bytes = 0
        reserved_output_bytes = 0
        for active in active_rows:
            active_payload = _parse_queued(active, now=now)
            if active_payload is not None:
                queued_bytes += active_payload.admission_bytes
                reserved_output_bytes += active_payload.render_options.max_output_bytes
        if queued_bytes + admission_bytes > self.queued_byte_limit:
            raise StudioRenderServiceError(
                429,
                "studio_queued_bytes",
                "The tenant Studio queued-byte limit is reached.",
            )
        live_usage = (
            await self.db.execute(
                select(
                    func.count(StudioRenderArtifact.id),
                    func.coalesce(func.sum(StudioRenderArtifact.byte_size), 0),
                ).where(
                    StudioRenderArtifact.tenant_id == self.tenant_id,
                    StudioRenderArtifact.storage_state.in_(
                        {"active", "delete_pending"}
                    ),
                )
            )
        ).one()
        live_count = int(live_usage[0] or 0)
        live_bytes = int(live_usage[1] or 0)
        if (
            live_count + len(active_rows) + 1 > self.live_artifact_limit
            or live_bytes
            + reserved_output_bytes
            + request.render_options.max_output_bytes
            > self.live_byte_limit
        ):
            raise StudioRenderServiceError(
                429,
                "studio_artifact_storage",
                "The tenant Studio artifact storage limit is reached.",
            )
        source = request.source
        options_sha256 = request.render_options.sha256
        manifest_sha256 = renderer_manifest.sha256
        effective_request_sha256 = canonical_effective_render_request_hash(
            request_sha256=request.request_sha256,
            input_binding_sha256=binding_sha256,
            input_binding_version=binding_version,
        )
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
            effective_request_sha256=effective_request_sha256,
            requested_by=request.requested_by,
            input_binding_id=request.input_binding_id,
            input_binding_sha256=binding_sha256,
            input_binding_version=binding_version,
            renderer_manifest=renderer_manifest,
            runtime_manifest_sha256=manifest_sha256,
            cache_key=_render_cache_key(
                kind=request.kind,
                draft_id=request.draft_id,
                rendered_revision=request.expected_revision,
                identity_sha256=request.identity_sha256,
                snapshot_id=request.snapshot_id,
                snapshot_content_sha256=request.content_sha256,
                source=source,
                render_options_sha256=options_sha256,
                effective_request_sha256=effective_request_sha256,
                input_binding_id=request.input_binding_id,
                input_binding_sha256=binding_sha256,
                input_binding_version=binding_version,
                runtime_manifest_sha256=manifest_sha256,
            ),
            admission_bytes=admission_bytes,
            expires_at=now + self.job_ttl,
        )
        row = DurableJob(
            tenant_id=self.tenant_id,
            kind=request.kind,
            idempotency_key=scope,
            payload=queued.model_dump(mode="json"),
            max_attempts=5,
            available_at=now,
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        await self.db.flush()
        accepted = StudioRenderAccepted(
            job_id=row.id,
            status_url=f"{status_base_url.rstrip('/')}/{row.id}",
            job_expires_at=queued.expires_at,
        )
        if audit is not None:
            await audit("studio_render_enqueued", accepted.job_id)
        return accepted

    async def enqueue(
        self,
        request: StudioRenderRequest,
        *,
        idempotency_key: str,
        status_base_url: str = "/api/template-studio/render-jobs",
        audit: StudioConsumerAudit | None = None,
    ) -> StudioRenderAccepted:
        """Stage and flush admission inside the caller-owned audit transaction."""

        return await self._enqueue_impl(
            request,
            idempotency_key=idempotency_key,
            status_base_url=status_base_url,
            audit=audit,
        )

    async def enqueue_test_render(
        self,
        request: StudioRenderRequest,
        *,
        idempotency_key: str,
        status_base_url: str = "/api/template-studio/render-jobs",
        audit: StudioConsumerAudit | None = None,
    ) -> StudioRenderAccepted:
        if request.kind != "studio_test_render":
            raise StudioRenderServiceError(
                422, "invalid_job_kind", "A test-render request is required."
            )
        return await self.enqueue(
            request,
            idempotency_key=idempotency_key,
            status_base_url=status_base_url,
            audit=audit,
        )

    async def _is_live_preferred_evidence(
        self,
        queued: _QueuedPayload,
        artifact_id: uuid.UUID | None,
        job_id: uuid.UUID,
        adoption_outcome: str | None,
    ) -> bool | None:
        """Resolve the exact preferred artifact, not revision identity alone."""

        if adoption_outcome is None:
            return None
        if (
            adoption_outcome != "current_evidence"
            or artifact_id is None
            or not _can_become_preferred_evidence(queued)
        ):
            return False
        draft = await self.db.scalar(
            select(StudioDraft).where(
                StudioDraft.id == queued.draft_id,
                StudioDraft.tenant_id == self.tenant_id,
            )
        )
        preferred = await self.db.scalar(
            select(StudioPreferredRenderEvidence).where(
                StudioPreferredRenderEvidence.tenant_id == self.tenant_id,
                StudioPreferredRenderEvidence.draft_id == queued.draft_id,
                StudioPreferredRenderEvidence.evidence_basis_sha256
                == _evidence_basis_sha256(queued),
            )
        )
        return bool(
            draft is not None
            and preferred is not None
            and draft.lifecycle_state == "active"
            and draft.cancellation_requested_at is None
            and draft.revision == queued.rendered_revision
            and draft.identity_sha256 == queued.identity_sha256
            and draft.evidence_revision == queued.rendered_revision
            and preferred.artifact_id == artifact_id
            and preferred.job_id == job_id
            and preferred.revision == queued.rendered_revision
            and preferred.identity_sha256 == queued.identity_sha256
        )

    async def _prepare_preferred_evidence_replacement(
        self,
        *,
        queued: _QueuedPayload,
        output_byte_size: int,
        now: datetime,
        artifact_ttl_seconds: int,
        metadata_ttl_seconds: int,
    ) -> None:
        """Serialize quota admission and retire the prior artifact in this slot."""

        if not _can_become_preferred_evidence(queued):
            raise RuntimeError("unsupported Studio preferred-evidence kind")
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"{self.tenant_id}:studio-retained-evidence"},
        )
        basis = _evidence_basis_sha256(queued)
        preferred = await self.db.scalar(
            select(StudioPreferredRenderEvidence)
            .where(
                StudioPreferredRenderEvidence.tenant_id == self.tenant_id,
                StudioPreferredRenderEvidence.draft_id == queued.draft_id,
                StudioPreferredRenderEvidence.evidence_basis_sha256 == basis,
            )
            .with_for_update()
        )
        previous = None
        if preferred is not None:
            previous = await self.db.scalar(
                select(StudioRenderArtifact)
                .where(
                    StudioRenderArtifact.tenant_id == self.tenant_id,
                    StudioRenderArtifact.id == preferred.artifact_id,
                )
                .with_for_update()
            )
        usage = (
            await self.db.execute(
                select(
                    func.count(StudioRenderArtifact.id),
                    func.coalesce(func.sum(StudioRenderArtifact.byte_size), 0),
                ).where(
                    StudioRenderArtifact.tenant_id == self.tenant_id,
                    StudioRenderArtifact.retention_class == "evidence",
                    StudioRenderArtifact.storage_state != "deleted",
                )
            )
        ).one()
        retained_count = int(usage[0] or 0)
        retained_bytes = int(usage[1] or 0)
        replacing = bool(
            previous is not None
            and previous.retention_class == "evidence"
            and previous.storage_state != "deleted"
        )
        next_count = retained_count - int(replacing) + 1
        next_bytes = (
            retained_bytes
            - (int(previous.byte_size) if replacing and previous is not None else 0)
            + output_byte_size
        )
        if (
            next_count > self.retained_artifact_limit
            or next_bytes > self.retained_byte_limit
        ):
            raise StudioRenderServiceError(
                429,
                "studio_retained_artifacts",
                "The tenant Studio retained-artifact limit is reached.",
            )
        if previous is not None and previous.storage_state != "deleted":
            previous.retention_class = "review"
            previous.content_expires_at = now + timedelta(seconds=artifact_ttl_seconds)
            previous.metadata_expires_at = now + timedelta(seconds=metadata_ttl_seconds)

    async def _stage_preferred_evidence(
        self,
        *,
        queued: _QueuedPayload,
        artifact: StudioRenderArtifact,
        now: datetime,
    ) -> None:
        preferred = await self.db.scalar(
            select(StudioPreferredRenderEvidence)
            .where(
                StudioPreferredRenderEvidence.tenant_id == self.tenant_id,
                StudioPreferredRenderEvidence.draft_id == queued.draft_id,
                StudioPreferredRenderEvidence.evidence_basis_sha256
                == artifact.evidence_basis_sha256,
            )
            .with_for_update()
        )
        if preferred is None:
            preferred = StudioPreferredRenderEvidence(
                tenant_id=self.tenant_id,
                draft_id=queued.draft_id,
                evidence_basis_sha256=artifact.evidence_basis_sha256,
                artifact_id=artifact.id,
                job_id=artifact.job_id,
                revision=queued.rendered_revision,
                identity_sha256=queued.identity_sha256,
                updated_at=now,
            )
            self.db.add(preferred)
        else:
            preferred.artifact_id = artifact.id
            preferred.job_id = artifact.job_id
            preferred.revision = queued.rendered_revision
            preferred.identity_sha256 = queued.identity_sha256
            preferred.updated_at = now
        await self.db.flush()

    async def _completed_artifact_availability(
        self,
        row: DurableJob,
        queued: _QueuedPayload,
        result: _PersistedResult,
    ) -> tuple[str, str, str, datetime | None, datetime | None] | None:
        """Verify immutable evidence and classify only legitimate expiry."""

        if result.artifact_id is None:
            return False
        artifact = await self.db.scalar(
            select(StudioRenderArtifact)
            .where(
                StudioRenderArtifact.id == result.artifact_id,
                StudioRenderArtifact.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if artifact is None:
            now = await self._clock_now()
            if (
                result.metadata_expires_at is not None
                and result.metadata_expires_at <= now
            ):
                return (
                    "expired",
                    "expired",
                    result.retention_class or "review",
                    result.content_expires_at,
                    result.metadata_expires_at,
                )
            return None
        # Artifact acquisition can block behind retention. Re-fence expiry
        # with wall-clock database time while both evidence rows are locked.
        now = await self._clock_now()
        content_sha256 = str(artifact.content_sha256 or "")
        if (
            re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None
            or artifact.artifact_kind not in _MEDIA_TYPES_BY_ARTIFACT_KIND
            or not _is_aware_database_time(artifact.created_at)
            or (
                artifact.content_expires_at is not None
                and not _is_aware_database_time(artifact.content_expires_at)
            )
            or (
                artifact.metadata_expires_at is not None
                and not _is_aware_database_time(artifact.metadata_expires_at)
            )
        ):
            return None
        expected_object_key = f"studio-content/v1/{content_sha256[:2]}/{content_sha256}"
        try:
            geometry_manifest = StudioGeometryManifest.model_validate(
                artifact.geometry_manifest
            )
        except (ValidationError, TypeError, ValueError):
            return None
        exact = bool(
            artifact.job_id == row.id
            and artifact.draft_id == queued.draft_id
            and artifact.snapshot_id == queued.snapshot_id
            and artifact.source_artifact_id == queued.source.artifact_id
            and artifact.requested_by_user_id == queued.requested_by
            and artifact.revision == queued.rendered_revision
            and artifact.identity_sha256 == queued.identity_sha256
            and artifact.evidence_basis_sha256 == _evidence_basis_sha256(queued)
            and artifact.snapshot_content_sha256 == queued.snapshot_content_sha256
            and artifact.source_sha256 == queued.source.sha256
            and artifact.source_media_type == queued.source.media_type
            and artifact.source_format == queued.source.format
            and artifact.request_sha256 == queued.request_sha256
            and artifact.effective_request_sha256 == queued.effective_request_sha256
            and artifact.render_options == queued.render_options.model_dump(mode="json")
            and artifact.render_options_sha256 == queued.render_options_sha256
            and artifact.requested_page_number == queued.render_options.page_number
            and artifact.requested_page_range_start is None
            and artifact.requested_page_range_end is None
            and artifact.cache_key == queued.cache_key
            and artifact.artifact_kind == _ARTIFACT_KIND_BY_JOB[queued.kind]
            and content_sha256 == result.output_sha256
            and artifact.object_key == expected_object_key
            and artifact.media_type == result.media_type
            and artifact.media_type
            in _MEDIA_TYPES_BY_ARTIFACT_KIND[artifact.artifact_kind]
            and artifact.byte_size == result.byte_size
            and artifact.runtime_manifest
            == queued.renderer_manifest.model_dump(mode="json")
            and artifact.runtime_manifest_sha256 == queued.runtime_manifest_sha256
            and artifact.input_binding_sha256
            == queued.input_binding_sha256
            == result.input_binding_sha256
            and artifact.input_binding_version
            == queued.input_binding_version
            == result.input_binding_version
            and artifact.artifact_page_count == result.artifact_page_count
            and artifact.document_page_count == result.document_page_count
            and artifact.geometry_manifest_sha256 == result.geometry_manifest_sha256
            and geometry_manifest.sha256 == result.geometry_manifest_sha256
            and _geometry_matches_request(
                geometry_manifest,
                artifact_kind=artifact.artifact_kind,
                requested_page_number=artifact.requested_page_number,
            )
            and artifact.adoption_outcome == result.adoption_outcome
        )
        if not exact:
            return None
        active = bool(
            artifact.storage_state == "active"
            and artifact.delete_requested_at is None
            and artifact.deleted_at is None
        )
        delete_pending = bool(
            artifact.storage_state == "delete_pending"
            and _is_aware_database_time(artifact.delete_requested_at)
            and artifact.deleted_at is None
        )
        deleted = bool(
            artifact.storage_state == "deleted"
            and _is_aware_database_time(artifact.delete_requested_at)
            and _is_aware_database_time(artifact.deleted_at)
        )
        if not (active or delete_pending or deleted):
            return None
        live_preferred = await self._is_live_preferred_evidence(
            queued,
            result.artifact_id,
            row.id,
            result.adoption_outcome,
        )
        preserved = artifact.legal_hold_at is not None or live_preferred is True
        content_expired = (
            artifact.content_expires_at is not None
            and artifact.content_expires_at <= now
            and not preserved
        )
        metadata_expired = (
            artifact.metadata_expires_at is not None
            and artifact.metadata_expires_at <= now
            and not preserved
        )
        if not active and not content_expired:
            return None
        content_availability = (
            "expired" if content_expired or not active else "available"
        )
        metadata_availability = "expired" if metadata_expired else "available"
        return (
            content_availability,
            metadata_availability,
            artifact.retention_class,
            artifact.content_expires_at,
            artifact.metadata_expires_at,
        )

    async def status(self, job_id: uuid.UUID) -> StudioRenderJobStatus:
        if self.actor_user_id is None:
            raise StudioRenderServiceError(
                403, "actor_mismatch", "Studio actor binding is invalid."
            )
        await self._bind_tenant_context()
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
            raise StudioRenderServiceError(
                404, "job_not_found", "Studio job not found."
            )
        if _peek_requested_by(row) != self.actor_user_id:
            raise StudioRenderServiceError(
                404, "job_not_found", "Studio job not found."
            )
        now = await self._clock_now()
        queued = _parse_queued(row, now=now)
        if queued is None:
            await self.db.flush()
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
                durable_state_changed=True,
            )
        if row.status in _ACTIVE_STATES and queued.expires_at <= now:
            if row.status == "cancel_requested":
                _transition(row, "cancelled", now=now)
            else:
                error_code, error_message = sanitized_failure("expired")
                row.last_error = error_message
                row.result = {"error_code": error_code}
                _transition(row, "failed", now=now)
        state = _status_state(row, now)
        result = _parse_result(row, now=now)
        if result is None:
            await self.db.flush()
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
                durable_state_changed=True,
            )
        invalid_shape = (
            (row.kind != queued.kind)
            or (
                not all(
                    _is_aware_database_time(value)
                    for value in (row.available_at, row.created_at, row.updated_at)
                )
            )
            or (
                row.leased_at is not None and not _is_aware_database_time(row.leased_at)
            )
            or (
                row.completed_at is not None
                and not _is_aware_database_time(row.completed_at)
            )
            or (state == "completed" and result.artifact_id is None)
            or (state == "failed" and result.error_code is None)
            or (
                state not in {"completed", "failed"}
                and (result.artifact_id is not None or result.error_code is not None)
            )
            or (
                state == "completed"
                and (
                    result.input_binding_sha256 != queued.input_binding_sha256
                    or result.input_binding_version != queued.input_binding_version
                )
            )
            or ((state in _TERMINAL_STATES) != (row.completed_at is not None))
            or (
                (state in _TERMINAL_STATES)
                != (max(0, min(100, int(row.progress or 0))) == 100)
            )
            or (
                (row.leased_at is not None)
                != (state in {"running", "cancel_requested"})
            )
            or (
                not (
                    1 <= int(row.max_attempts or 0) <= 100
                    and 0 <= int(row.attempts or 0) <= int(row.max_attempts or 0)
                )
            )
        )
        if invalid_shape:
            _terminalize_poison(row, now=now)
            await self.db.flush()
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
                durable_state_changed=True,
            )
        artifact_availability = None
        artifact_metadata_availability = None
        current_retention_class = (
            result.retention_class if state == "completed" else None
        )
        current_content_expires_at = (
            result.content_expires_at if state == "completed" else None
        )
        current_metadata_expires_at = (
            result.metadata_expires_at if state == "completed" else None
        )
        if state == "completed":
            availability = await self._completed_artifact_availability(
                row, queued, result
            )
            if availability is not None:
                (
                    artifact_availability,
                    artifact_metadata_availability,
                    current_retention_class,
                    current_content_expires_at,
                    current_metadata_expires_at,
                ) = availability
        if state == "completed" and artifact_metadata_availability is None:
            _terminalize_poison(row, now=now)
            await self.db.flush()
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
                durable_state_changed=True,
            )
        error_code = error_message = None
        if state == "failed":
            error_code, error_message = sanitized_failure(result.error_code or "")
        artifact_id = result.artifact_id if state == "completed" else None
        status_url = f"/api/template-studio/render-jobs/{row.id}"
        expose_metadata = artifact_metadata_availability == "available"
        result_url = (
            f"/api/template-studio/render-artifacts/{artifact_id}"
            if artifact_id is not None and expose_metadata
            else None
        )
        download_url = f"{result_url}/content" if result_url is not None else None
        geometry_url = f"{result_url}/geometry" if result_url is not None else None
        live_current_evidence = await self._is_live_preferred_evidence(
            queued,
            artifact_id,
            row.id,
            result.adoption_outcome if state == "completed" else None,
        )
        response = StudioRenderJobStatus(
            job_id=row.id,
            status_url=status_url,
            kind=queued.kind,
            state=state,
            progress=max(0, min(100, int(row.progress or 0))),
            attempts=int(row.attempts or 0),
            max_attempts=int(row.max_attempts or 1),
            retry_at=(
                row.available_at
                if state == "retry_wait" and row.available_at > now
                else None
            ),
            created_at=row.created_at,
            updated_at=row.updated_at,
            leased_at=row.leased_at,
            completed_at=row.completed_at,
            draft_id=queued.draft_id,
            rendered_revision=queued.rendered_revision,
            identity_sha256=queued.identity_sha256,
            snapshot_id=queued.snapshot_id,
            snapshot_content_sha256=queued.snapshot_content_sha256,
            source=queued.source,
            render_options=queued.render_options,
            render_options_sha256=queued.render_options_sha256,
            request_sha256=queued.request_sha256,
            effective_request_sha256=queued.effective_request_sha256,
            renderer_manifest=queued.renderer_manifest,
            runtime_manifest_sha256=queued.runtime_manifest_sha256,
            input_binding_sha256=queued.input_binding_sha256,
            input_binding_version=queued.input_binding_version,
            artifact_id=artifact_id,
            artifact_availability=artifact_availability,
            artifact_metadata_availability=artifact_metadata_availability,
            result_url=result_url,
            download_url=download_url,
            adoption_outcome=result.adoption_outcome if state == "completed" else None,
            geometry_url=geometry_url,
            adopted_as_preferred_evidence=(
                result.preferred_evidence_at_completion
                if state == "completed"
                else None
            ),
            is_preferred_evidence=live_current_evidence,
            auto_open=(
                live_current_evidence is True
                and artifact_availability == "available"
                and expose_metadata
            ),
            output_sha256=(
                result.output_sha256
                if state == "completed" and expose_metadata
                else None
            ),
            output_media_type=(
                result.media_type if state == "completed" and expose_metadata else None
            ),
            output_byte_size=(
                result.byte_size if state == "completed" and expose_metadata else None
            ),
            artifact_page_count=(
                result.artifact_page_count
                if state == "completed" and expose_metadata
                else None
            ),
            document_page_count=(
                result.document_page_count
                if state == "completed" and expose_metadata
                else None
            ),
            geometry_manifest_sha256=(
                result.geometry_manifest_sha256
                if state == "completed" and expose_metadata
                else None
            ),
            retention_class=current_retention_class,
            error_code=error_code,
            error_message=error_message,
            error_retryable=(
                STUDIO_PUBLIC_ERROR_RETRYABLE[error_code]
                if error_code is not None
                else None
            ),
            job_expires_at=queued.expires_at,
            content_expires_at=current_content_expires_at,
            metadata_expires_at=current_metadata_expires_at,
        )
        await self.db.flush()
        return response

    async def artifact_result(self, artifact_id: uuid.UUID) -> StudioRenderJobStatus:
        """Return available result metadata without exposing artifact ORM rows."""

        await self._bind_tenant_context()
        job_id = await self.db.scalar(
            select(StudioRenderArtifact.job_id).where(
                StudioRenderArtifact.id == artifact_id,
                StudioRenderArtifact.tenant_id == self.tenant_id,
            )
        )
        if job_id is None:
            raise StudioRenderServiceError(
                404, "artifact_not_found", "Studio artifact not found."
            )
        try:
            status = await self.status(job_id)
        except StudioRenderServiceError as exc:
            if exc.code == "job_not_found":
                raise StudioRenderServiceError(
                    404, "artifact_not_found", "Studio artifact not found."
                ) from exc
            raise
        if status.artifact_id != artifact_id:
            raise StudioRenderServiceError(
                404, "artifact_not_found", "Studio artifact not found."
            )
        if status.artifact_metadata_availability == "expired":
            raise StudioRenderServiceError(
                410, "artifact_expired", "The Studio artifact has expired."
            )
        return status

    async def artifact_geometry(self, artifact_id: uuid.UUID) -> StudioArtifactGeometry:
        """Return authenticated geometry even after content bytes expire."""

        status = await self.artifact_result(artifact_id)
        if status.geometry_manifest_sha256 is None:
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
            )
        artifact = await self.db.scalar(
            select(StudioRenderArtifact).where(
                StudioRenderArtifact.id == artifact_id,
                StudioRenderArtifact.tenant_id == self.tenant_id,
            )
        )
        if artifact is None:
            raise StudioRenderServiceError(
                404, "artifact_not_found", "Studio artifact not found."
            )
        try:
            geometry = StudioGeometryManifest.model_validate(artifact.geometry_manifest)
            result = StudioArtifactGeometry(
                artifact_id=artifact_id,
                geometry_manifest=geometry,
                geometry_manifest_sha256=artifact.geometry_manifest_sha256,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
            ) from exc
        if result.geometry_manifest_sha256 != status.geometry_manifest_sha256:
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
            )
        return result

    async def _quarantine_storage_failure(self, artifact_id: uuid.UUID) -> None:
        """Revoke preferred evidence before reporting verified-read corruption."""

        await self._bind_tenant_context()
        artifact = await self.db.scalar(
            select(StudioRenderArtifact)
            .where(
                StudioRenderArtifact.id == artifact_id,
                StudioRenderArtifact.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if artifact is None:
            return
        now = await self._clock_now()
        preferred = await self.db.scalar(
            select(StudioPreferredRenderEvidence)
            .where(
                StudioPreferredRenderEvidence.tenant_id == self.tenant_id,
                StudioPreferredRenderEvidence.draft_id == artifact.draft_id,
                StudioPreferredRenderEvidence.artifact_id == artifact.id,
                StudioPreferredRenderEvidence.job_id == artifact.job_id,
            )
            .with_for_update()
        )
        if preferred is not None:
            await self.db.delete(preferred)
            draft = await self.db.scalar(
                select(StudioDraft)
                .where(
                    StudioDraft.id == artifact.draft_id,
                    StudioDraft.tenant_id == self.tenant_id,
                )
                .with_for_update()
            )
            if (
                draft is not None
                and draft.evidence_revision == artifact.revision
                and draft.identity_sha256 == artifact.identity_sha256
            ):
                draft.evidence_revision = None
                draft.evidence_invalidated_at = now
                draft.evidence_invalidation_reason = "render_storage_integrity_failed"
        if artifact.storage_state == "active":
            artifact.storage_state = "delete_pending"
            artifact.delete_requested_at = now
        artifact.retention_class = "review"
        artifact.content_expires_at = now
        if artifact.metadata_expires_at is None or artifact.metadata_expires_at <= now:
            artifact.metadata_expires_at = now + timedelta(days=30)
        await self.db.flush()

    async def artifact_content(
        self,
        artifact_id: uuid.UUID,
        *,
        object_store: StudioObjectStore,
        max_bytes: int,
    ) -> StudioRenderArtifactContent:
        """Read verified output with post-I/O reauthorization and expiry fencing."""

        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 1 <= max_bytes <= 100 * 1024 * 1024
        ):
            raise ValueError("artifact content bound is invalid")
        status = await self.artifact_result(artifact_id)
        if status.artifact_availability == "expired":
            raise StudioRenderServiceError(
                410, "artifact_expired", "The Studio artifact has expired."
            )
        if (
            status.output_sha256 is None
            or status.output_media_type is None
            or status.output_byte_size is None
        ):
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
            )
        if status.output_byte_size > max_bytes:
            raise StudioRenderServiceError(
                413,
                "download_limit_exceeded",
                "The Studio artifact exceeds the download limit.",
            )
        ref = StudioObjectRef(
            tenant_id=self.tenant_id,
            object_key=(
                f"studio-content/v1/{status.output_sha256[:2]}/"
                f"{status.output_sha256}"
            ),
            sha256=status.output_sha256,
            byte_size=status.output_byte_size,
            media_type=status.output_media_type,
        )
        # Result/content reads own no consumer mutation. Release status locks
        # before potentially slow storage I/O, then reauthorize and re-fence.
        await self.db.rollback()
        try:
            content = await run_storage_operation_to_completion(
                partial(
                    object_store.read,
                    ref,
                    max_bytes=status.output_byte_size,
                ),
                timeout_seconds=_STORAGE_READ_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            await self.artifact_result(artifact_id)
            raise StudioRenderServiceError(
                504,
                "processor_timeout",
                STUDIO_PUBLIC_FAILURES["processor_timeout"],
            ) from exc
        except Exception as exc:
            await self._quarantine_storage_failure(artifact_id)
            raise StudioRenderServiceError(
                409,
                "storage_integrity_failed",
                STUDIO_PUBLIC_FAILURES["storage_integrity_failed"],
                durable_state_changed=True,
            ) from exc
        fresh_status = await self.artifact_result(artifact_id)
        if fresh_status.artifact_availability == "expired":
            raise StudioRenderServiceError(
                410, "artifact_expired", "The Studio artifact has expired."
            )
        if (
            fresh_status.output_sha256 != status.output_sha256
            or fresh_status.output_media_type != status.output_media_type
            or fresh_status.output_byte_size != status.output_byte_size
        ):
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
            )
        return StudioRenderArtifactContent(
            artifact_id=artifact_id,
            content=content,
            sha256=status.output_sha256,
            media_type=status.output_media_type,
        )

    async def request_cancel(
        self,
        job_id: uuid.UUID,
        *,
        audit: StudioConsumerAudit | None = None,
    ) -> StudioRenderJobStatus:
        if self.actor_user_id is None:
            raise StudioRenderServiceError(
                403, "actor_mismatch", "Studio actor binding is invalid."
            )
        await self._bind_tenant_context()
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
            raise StudioRenderServiceError(
                404, "job_not_found", "Studio job not found."
            )
        if _peek_requested_by(row) != self.actor_user_id:
            raise StudioRenderServiceError(
                404, "job_not_found", "Studio job not found."
            )
        now = await self._clock_now()
        queued = _parse_queued(row, now=now)
        if queued is None:
            await self.db.flush()
            raise StudioRenderServiceError(
                409,
                "job_data_unavailable",
                STUDIO_PUBLIC_FAILURES["job_data_unavailable"],
                durable_state_changed=True,
            )
        prior_status = row.status
        if row.status == "pending":
            _transition(row, "cancelled", now=now)
        elif row.status == "running":
            _transition(row, "cancel_requested", now=now)
        if row.status != prior_status and audit is not None:
            await audit("studio_render_cancel_requested", row.id)
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
        await self._bind_tenant_context()
        if not await self._acquire_tenant_purge_fence():
            await self.db.rollback()
            return None
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == job_id,
                DurableJob.tenant_id == self.tenant_id,
                DurableJob.kind.in_(STUDIO_RENDER_JOB_KINDS),
            )
            .with_for_update(skip_locked=True)
        )
        now = await self._clock_now()
        if row is None:
            await self.db.rollback()
            return None
        queued = _parse_queued(row, now=now)
        if queued is None:
            await self.db.commit()
            return None
        if row.result is not None and _parse_result(row, now=now) is None:
            await self.db.commit()
            return None
        if row.status in _TERMINAL_STATES or row.status not in _ACTIVE_STATES:
            await self.db.rollback()
            return None
        if row.status == "cancel_requested":
            stale_lease = (
                queued.lease_expires_at is None or queued.lease_expires_at <= now
            )
            if stale_lease:
                _transition(row, "cancelled", now=now)
                await self.db.commit()
            else:
                await self.db.rollback()
            return None
        if queued.expires_at <= now:
            code, message = sanitized_failure("expired")
            row.last_error = message
            row.result = {"error_code": code}
            _transition(row, "failed", now=now)
            await self.db.commit()
            return None
        stale_lease = queued.lease_expires_at is None or queued.lease_expires_at <= now
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
        row.updated_at = now
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

    @staticmethod
    def _owns_live(
        row: DurableJob | None,
        lease: StudioJobLease,
        *,
        now: datetime,
    ) -> bool:
        if not _StudioRenderJobStore._owns(row, lease):
            return False
        try:
            queued = _QueuedPayload.model_validate(row.payload)
        except Exception:
            return False
        return queued.lease_expires_at is not None and queued.lease_expires_at > now

    async def renew_lease(self, lease: StudioJobLease) -> bool:
        await self._bind_tenant_context()
        if not await self._acquire_tenant_purge_fence():
            await self.db.rollback()
            return False
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == lease.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        now = await self._clock_now()
        if not self._owns_live(row, lease, now=now) or row.status != "running":
            await self.db.rollback()
            return False
        queued = _QueuedPayload.model_validate(row.payload)
        draft_state = (
            await self.db.execute(
                select(
                    StudioDraft.lifecycle_state,
                    StudioDraft.cancellation_requested_at,
                ).where(
                    StudioDraft.id == queued.draft_id,
                    StudioDraft.tenant_id == self.tenant_id,
                )
            )
        ).one_or_none()
        if (
            draft_state is None
            or draft_state.lifecycle_state != "active"
            or draft_state.cancellation_requested_at is not None
        ):
            _transition(row, "cancel_requested", now=now)
            await self.db.commit()
            return False
        if queued.expires_at <= now:
            code, message = sanitized_failure("expired")
            row.last_error = message
            row.result = {"error_code": code}
            _transition(row, "failed", now=now)
            await self.db.commit()
            return False
        if queued.lease_duration_seconds is None:
            await self.db.rollback()
            return False
        queued.lease_expires_at = now + timedelta(seconds=queued.lease_duration_seconds)
        row.payload = queued.model_dump(mode="json")
        row.leased_at = now
        row.updated_at = now
        await self.db.commit()
        return True

    async def update_progress(self, lease: StudioJobLease, progress: int) -> bool:
        await self._bind_tenant_context()
        row = await self.db.scalar(
            select(DurableJob)
            .where(
                DurableJob.id == lease.job_id,
                DurableJob.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        now = await self._clock_now()
        if not self._owns_live(row, lease, now=now) or row.status != "running":
            await self.db.rollback()
            return False
        queued = _QueuedPayload.model_validate(row.payload)
        if queued.expires_at <= now:
            code, message = sanitized_failure("expired")
            row.last_error = message
            row.result = {"error_code": code}
            _transition(row, "failed", now=now)
            await self.db.commit()
            return False
        row.progress = max(int(row.progress or 0), min(95, int(progress)))
        row.updated_at = now
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
        now = await self._clock_now()
        if not self._owns_live(row, lease, now=now):
            await self.db.rollback()
            return False
        public_code, public_message = sanitized_failure(code)
        queued = _QueuedPayload.model_validate(row.payload)
        if row.status != "cancel_requested" and queued.expires_at <= now:
            public_code, public_message = sanitized_failure("expired")
        row.last_error = public_message
        if row.status == "cancel_requested" or public_code == "cancelled":
            row.result = None
            _transition(row, "cancelled", now=now)
        elif public_code == "expired":
            row.result = {"error_code": public_code}
            _transition(row, "failed", now=now)
        elif retryable and row.attempts < row.max_attempts:
            row.result = None
            _transition(row, "pending", now=now)
            row.available_at = now + timedelta(
                seconds=min(3600, 15 * (2 ** max(1, row.attempts)))
            )
            row.leased_at = None
            row.lease_owner = None
        else:
            row.result = {"error_code": public_code}
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
        content_expires_at: datetime | None,
        metadata_expires_at: datetime | None,
        artifact_page_count: int,
        document_page_count: int,
        geometry_manifest: StudioGeometryManifest,
        geometry_manifest_sha256: str,
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
            evidence_basis_sha256=_evidence_basis_sha256(queued),
            snapshot_content_sha256=queued.snapshot_content_sha256,
            source_sha256=queued.source.sha256,
            source_media_type=queued.source.media_type,
            source_format=queued.source.format,
            request_sha256=queued.request_sha256,
            effective_request_sha256=queued.effective_request_sha256,
            render_options=queued.render_options.model_dump(mode="json"),
            render_options_sha256=queued.render_options_sha256,
            requested_page_number=queued.render_options.page_number,
            requested_page_range_start=None,
            requested_page_range_end=None,
            cache_key=queued.cache_key,
            artifact_kind=artifact_kind,
            content_sha256=output.sha256,
            byte_size=output.byte_size,
            media_type=output.media_type,
            object_key=output.object_key,
            runtime_manifest=queued.renderer_manifest.model_dump(mode="json"),
            runtime_manifest_sha256=queued.runtime_manifest_sha256,
            input_binding_sha256=queued.input_binding_sha256,
            input_binding_version=queued.input_binding_version,
            artifact_page_count=artifact_page_count,
            document_page_count=document_page_count,
            geometry_manifest=geometry_manifest.model_dump(mode="json"),
            geometry_manifest_sha256=geometry_manifest_sha256,
            adoption_outcome=adoption_outcome,
            retention_class=retention_class,
            content_expires_at=content_expires_at,
            metadata_expires_at=metadata_expires_at,
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
        content_expires_at: datetime | None,
        metadata_expires_at: datetime | None,
        artifact_page_count: int,
        document_page_count: int,
        geometry_manifest: StudioGeometryManifest,
        geometry_manifest_sha256: str,
        now: datetime,
        preferred_evidence_at_completion: bool = False,
    ) -> StudioRenderArtifact:
        artifact = self._new_artifact(
            row=row,
            queued=queued,
            output=output,
            artifact_kind=artifact_kind,
            adoption_outcome=adoption_outcome,
            retention_class=retention_class,
            content_expires_at=content_expires_at,
            metadata_expires_at=metadata_expires_at,
            artifact_page_count=artifact_page_count,
            document_page_count=document_page_count,
            geometry_manifest=geometry_manifest,
            geometry_manifest_sha256=geometry_manifest_sha256,
        )
        artifact.created_at = now
        self.db.add(artifact)
        await self.db.flush()
        if artifact.id is None:
            raise RuntimeError("Studio artifact ID was not materialized")
        row.result = {
            "artifact_id": str(artifact.id),
            "adoption_outcome": adoption_outcome,
            "preferred_evidence_at_completion": preferred_evidence_at_completion,
            "content_expires_at": (
                content_expires_at.isoformat()
                if content_expires_at is not None
                else None
            ),
            "metadata_expires_at": (
                metadata_expires_at.isoformat()
                if metadata_expires_at is not None
                else None
            ),
            "retention_class": retention_class,
            "output_sha256": output.sha256,
            "media_type": output.media_type,
            "byte_size": output.byte_size,
            "artifact_page_count": artifact_page_count,
            "document_page_count": document_page_count,
            "geometry_manifest_sha256": geometry_manifest_sha256,
            "input_binding_sha256": queued.input_binding_sha256,
            "input_binding_version": queued.input_binding_version,
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
        runtime_manifest_sha256: str,
        retention_class: str = "review",
        artifact_ttl_seconds: int,
        artifact_page_count: int,
        document_page_count: int,
        geometry_manifest: StudioGeometryManifest,
        geometry_manifest_sha256: str,
        metadata_ttl_seconds: int = 2_592_000,
    ) -> tuple[uuid.UUID, str]:
        """Materialize job-owned artifact evidence with a fenced exact adoption."""

        if output.tenant_id != self.tenant_id:
            raise StudioRenderServiceError(
                409, "storage_integrity_failed", "Studio output tenant is invalid."
            )
        if not await self._acquire_tenant_purge_fence():
            raise StudioRenderServiceError(
                409,
                "cancelled",
                "Studio processing was cancelled.",
            )
        if not 300 <= artifact_ttl_seconds <= 604_800:
            raise StudioRenderServiceError(
                409, "validation_failed", "Studio artifact TTL is invalid."
            )
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": (f"{self.tenant_id}:studio-object:{output.object_key}")},
        )
        # Admission reserves one max-sized output while the job is active.
        # Hold the same tenant lock while atomically exchanging that
        # reservation for the materialized artifact row.
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"{self.tenant_id}:studio-render-admission"},
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
        if output.media_type not in _MEDIA_TYPES_BY_ARTIFACT_KIND[artifact_kind]:
            raise StudioRenderServiceError(
                409, "validation_failed", "Studio output media type is invalid."
            )
        if not 1 <= artifact_page_count <= lease.payload.render_options.max_pages:
            raise StudioRenderServiceError(
                409,
                "validation_failed",
                "Studio output page metadata is invalid.",
            )
        if not 1 <= document_page_count <= lease.payload.render_options.max_pages:
            raise StudioRenderServiceError(
                409,
                "validation_failed",
                "Studio document page metadata is invalid.",
            )
        if (
            geometry_manifest.artifact_page_count != artifact_page_count
            or geometry_manifest.document_page_count != document_page_count
            or geometry_manifest.sha256 != geometry_manifest_sha256
            or not _geometry_matches_request(
                geometry_manifest,
                artifact_kind=artifact_kind,
                requested_page_number=lease.payload.render_options.page_number,
            )
        ):
            raise StudioRenderServiceError(
                409,
                "validation_failed",
                "Studio geometry metadata is invalid.",
            )
        if artifact_kind == "page_preview" and artifact_page_count != 1:
            raise StudioRenderServiceError(
                409, "validation_failed", "Studio page-preview metadata is invalid."
            )
        if retention_class not in _RETENTION_CLASSES:
            raise StudioRenderServiceError(
                409, "validation_failed", "Studio retention class is invalid."
            )
        if not 86_400 <= metadata_ttl_seconds <= 31_536_000:
            raise StudioRenderServiceError(
                409, "validation_failed", "Studio metadata TTL is invalid."
            )
        if (
            retention_class != "evidence"
            and metadata_ttl_seconds <= artifact_ttl_seconds
        ):
            raise StudioRenderServiceError(
                409, "validation_failed", "Studio retention windows are invalid."
            )
        if runtime_manifest_sha256 != lease.payload.runtime_manifest_sha256:
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
        now = await self._clock_now()
        if not self._owns_live(row, lease, now=now):
            raise StudioRenderServiceError(
                409, "lease_lost", "Studio job lease is no longer owned."
            )
        queued = _QueuedPayload.model_validate(row.payload)
        if queued.expires_at <= now:
            code, message = sanitized_failure("expired")
            row.last_error = message
            row.result = {"error_code": code}
            _transition(row, "failed", now=now)
            await self.db.commit()
            raise StudioRenderServiceError(409, code, message)

        input_state = await self._authoritative_inputs_state(queued)
        now = await self._clock_now()
        if not self._owns_live(row, lease, now=now):
            raise StudioRenderServiceError(
                409, "lease_lost", "Studio job lease is no longer owned."
            )
        preferred_evidence = bool(
            _can_become_preferred_evidence(queued)
            and row.status != "cancel_requested"
            and input_state.disposition == "current"
        )
        if preferred_evidence:
            await self._prepare_preferred_evidence_replacement(
                queued=queued,
                output_byte_size=output.byte_size,
                now=now,
                artifact_ttl_seconds=artifact_ttl_seconds,
                metadata_ttl_seconds=metadata_ttl_seconds,
            )
            retention_class = "evidence"
        elif retention_class == "evidence":
            retention_class = "review"
        content_expires_at = (
            None
            if retention_class == "evidence"
            else now + timedelta(seconds=artifact_ttl_seconds)
        )
        metadata_expires_at = (
            None
            if retention_class == "evidence"
            else now + timedelta(seconds=metadata_ttl_seconds)
        )
        if queued.expires_at <= now:
            code, message = sanitized_failure("expired")
            row.last_error = message
            row.result = {"error_code": code}
            _transition(row, "failed", now=now)
            await self.db.commit()
            raise StudioRenderServiceError(409, code, message)
        if input_state.disposition == "corrupt":
            await self._fail_corrupt_adoption(row, input_state, now=now)
        if row.status == "cancel_requested" or input_state.disposition == "cancelled":
            artifact = await self._materialize_terminal(
                row=row,
                queued=queued,
                output=output,
                artifact_kind=artifact_kind,
                adoption_outcome="cancelled_output",
                retention_class=retention_class,
                content_expires_at=content_expires_at,
                metadata_expires_at=metadata_expires_at,
                artifact_page_count=artifact_page_count,
                document_page_count=document_page_count,
                geometry_manifest=geometry_manifest,
                geometry_manifest_sha256=geometry_manifest_sha256,
                now=now,
            )
            await self.db.commit()
            return artifact.id, "cancelled_output"
        if input_state.disposition == "stale":
            artifact = await self._materialize_terminal(
                row=row,
                queued=queued,
                output=output,
                artifact_kind=artifact_kind,
                adoption_outcome="stale_output",
                retention_class=retention_class,
                content_expires_at=content_expires_at,
                metadata_expires_at=metadata_expires_at,
                artifact_page_count=artifact_page_count,
                document_page_count=document_page_count,
                geometry_manifest=geometry_manifest,
                geometry_manifest_sha256=geometry_manifest_sha256,
                now=now,
            )
            await self.db.commit()
            return artifact.id, "stale_output"

        artifact = await self._materialize_terminal(
            row=row,
            queued=queued,
            output=output,
            artifact_kind=artifact_kind,
            adoption_outcome="current_evidence",
            retention_class=retention_class,
            content_expires_at=content_expires_at,
            metadata_expires_at=metadata_expires_at,
            artifact_page_count=artifact_page_count,
            document_page_count=document_page_count,
            geometry_manifest=geometry_manifest,
            geometry_manifest_sha256=geometry_manifest_sha256,
            now=now,
            preferred_evidence_at_completion=preferred_evidence,
        )
        artifact_id = artifact.id
        if not preferred_evidence:
            await self.db.commit()
            return artifact_id, "current_evidence"
        await self._stage_preferred_evidence(
            queued=queued,
            artifact=artifact,
            now=now,
        )
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
            {"scope": (f"{self.tenant_id}:studio-object:{output.object_key}")},
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
        stale_now = await self._clock_now()
        if not self._owns_live(row, lease, now=stale_now):
            raise StudioRenderServiceError(
                409, "lease_lost", "Studio job lease is no longer owned."
            )
        queued = _QueuedPayload.model_validate(row.payload)
        if queued.expires_at <= stale_now:
            code, message = sanitized_failure("expired")
            row.last_error = message
            row.result = {"error_code": code}
            _transition(row, "failed", now=stale_now)
            await self.db.commit()
            raise StudioRenderServiceError(409, code, message)
        input_state = await self._authoritative_inputs_state(queued)
        stale_now = await self._clock_now()
        if not self._owns_live(row, lease, now=stale_now):
            raise StudioRenderServiceError(
                409, "lease_lost", "Studio job lease is no longer owned."
            )
        if queued.expires_at <= stale_now:
            code, message = sanitized_failure("expired")
            row.last_error = message
            row.result = {"error_code": code}
            _transition(row, "failed", now=stale_now)
            await self.db.commit()
            raise StudioRenderServiceError(409, code, message)
        if retention_class == "evidence":
            retention_class = "review"
        content_expires_at = (
            None
            if retention_class == "evidence"
            else stale_now + timedelta(seconds=artifact_ttl_seconds)
        )
        metadata_expires_at = (
            None
            if retention_class == "evidence"
            else stale_now + timedelta(seconds=metadata_ttl_seconds)
        )
        if input_state.disposition == "corrupt":
            await self._fail_corrupt_adoption(row, input_state, now=stale_now)
        if row.status == "cancel_requested" or input_state.disposition == "cancelled":
            artifact = await self._materialize_terminal(
                row=row,
                queued=queued,
                output=output,
                artifact_kind=artifact_kind,
                adoption_outcome="cancelled_output",
                retention_class=retention_class,
                content_expires_at=content_expires_at,
                metadata_expires_at=metadata_expires_at,
                artifact_page_count=artifact_page_count,
                document_page_count=document_page_count,
                geometry_manifest=geometry_manifest,
                geometry_manifest_sha256=geometry_manifest_sha256,
                now=stale_now,
            )
            await self.db.commit()
            return artifact.id, "cancelled_output"
        artifact = await self._materialize_terminal(
            row=row,
            queued=queued,
            output=output,
            artifact_kind=artifact_kind,
            adoption_outcome="stale_output",
            retention_class=retention_class,
            content_expires_at=content_expires_at,
            metadata_expires_at=metadata_expires_at,
            artifact_page_count=artifact_page_count,
            document_page_count=document_page_count,
            geometry_manifest=geometry_manifest,
            geometry_manifest_sha256=geometry_manifest_sha256,
            now=stale_now,
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
        runtime_manifest_sha256: str,
        retention_class: str = "review",
        artifact_ttl_seconds: int,
        artifact_page_count: int,
        document_page_count: int,
        geometry_manifest: StudioGeometryManifest,
        geometry_manifest_sha256: str,
        metadata_ttl_seconds: int = 2_592_000,
    ) -> tuple[uuid.UUID, str]:
        """Adopt with guaranteed release of row and advisory locks on errors."""

        try:
            return await self._adopt_output_impl(
                lease,
                output,
                object_store=object_store,
                artifact_kind=artifact_kind,
                runtime_manifest_sha256=runtime_manifest_sha256,
                retention_class=retention_class,
                artifact_ttl_seconds=artifact_ttl_seconds,
                artifact_page_count=artifact_page_count,
                document_page_count=document_page_count,
                geometry_manifest=geometry_manifest,
                geometry_manifest_sha256=geometry_manifest_sha256,
                metadata_ttl_seconds=metadata_ttl_seconds,
            )
        except BaseException:
            if self.db.in_transaction():
                await self.db.rollback()
            raise

    async def stage_and_adopt_output(
        self,
        lease: StudioJobLease,
        content: bytes,
        *,
        object_store: StudioObjectStore,
        media_type: str,
        content_sha256: str,
        artifact_kind: str,
        runtime_manifest_sha256: str,
        retention_class: str = "review",
        artifact_ttl_seconds: int,
        artifact_page_count: int,
        document_page_count: int,
        geometry_manifest: StudioGeometryManifest,
        geometry_manifest_sha256: str,
        metadata_ttl_seconds: int = 2_592_000,
    ) -> tuple[uuid.UUID, str, StudioStagedObject]:
        """Durably stage and adopt fresh bytes under the shared object lock."""

        if re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None:
            raise StudioRenderServiceError(
                409,
                "storage_integrity_failed",
                "Studio output failed its integrity check.",
            )
        if lease.payload.lease_expires_at is None:
            raise StudioRenderServiceError(
                409, "lease_lost", "Studio job lease is no longer owned."
            )
        object_key = f"studio-content/v1/{content_sha256[:2]}/{content_sha256}"
        try:
            if not await self._acquire_tenant_purge_fence():
                raise StudioRenderServiceError(
                    409,
                    "cancelled",
                    "Studio processing was cancelled.",
                )
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                {"scope": f"{self.tenant_id}:studio-object:{object_key}"},
            )
            staged = await run_storage_mutation_to_completion(
                partial(
                    object_store.stage,
                    self.tenant_id,
                    content,
                    job_id=lease.job_id,
                    lease_token=lease.token,
                    reconcile_after=lease.payload.lease_expires_at,
                    media_type=media_type,
                    expected_sha256=content_sha256,
                ),
                timeout_seconds=_STORAGE_STAGE_TIMEOUT_SECONDS,
            )
            artifact_id, outcome = await self._adopt_output_impl(
                lease,
                staged.object_ref,
                object_store=object_store,
                artifact_kind=artifact_kind,
                runtime_manifest_sha256=runtime_manifest_sha256,
                retention_class=retention_class,
                artifact_ttl_seconds=artifact_ttl_seconds,
                artifact_page_count=artifact_page_count,
                document_page_count=document_page_count,
                geometry_manifest=geometry_manifest,
                geometry_manifest_sha256=geometry_manifest_sha256,
                metadata_ttl_seconds=metadata_ttl_seconds,
            )
            return artifact_id, outcome, staged
        except BaseException:
            if self.db.in_transaction():
                await self.db.rollback()
            raise

    async def find_cached_output(
        self,
        cache_key: str,
        *,
        object_store: StudioObjectStore,
        max_bytes: int,
    ) -> StudioCachedOutput | None:
        if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
            return None
        instant = await self._clock_now()
        artifact = await self.db.scalar(
            select(StudioRenderArtifact)
            .where(
                StudioRenderArtifact.tenant_id == self.tenant_id,
                StudioRenderArtifact.cache_key == cache_key,
                StudioRenderArtifact.storage_state == "active",
                or_(
                    StudioRenderArtifact.content_expires_at.is_(None),
                    StudioRenderArtifact.content_expires_at > instant,
                ),
            )
            .order_by(StudioRenderArtifact.created_at.desc())
            .limit(1)
        )
        if artifact is None:
            return None
        object_key = artifact.object_key
        await self.db.rollback()
        await self._bind_tenant_context()
        # Global lock order: tenant/object advisory lock, artifact row, job row.
        # Retention and adoption use the same order.
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"{self.tenant_id}:studio-object:{object_key}"},
        )
        instant = await self._clock_now()
        artifact = await self.db.scalar(
            select(StudioRenderArtifact)
            .where(
                StudioRenderArtifact.tenant_id == self.tenant_id,
                StudioRenderArtifact.cache_key == cache_key,
                StudioRenderArtifact.object_key == object_key,
                StudioRenderArtifact.storage_state == "active",
                or_(
                    StudioRenderArtifact.content_expires_at.is_(None),
                    StudioRenderArtifact.content_expires_at > instant,
                ),
            )
            .with_for_update()
        )
        if artifact is None:
            await self.db.rollback()
            return None
        content_sha256 = str(artifact.content_sha256 or "")
        expected_object_key = f"studio-content/v1/{content_sha256[:2]}/{content_sha256}"
        if (
            artifact.artifact_page_count is None
            or artifact.document_page_count is None
            or artifact.artifact_kind not in _MEDIA_TYPES_BY_ARTIFACT_KIND
            or re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None
            or artifact.object_key != expected_object_key
            or not isinstance(artifact.byte_size, int)
            or artifact.byte_size < 1
            or not isinstance(artifact.media_type, str)
            or artifact.media_type
            not in _MEDIA_TYPES_BY_ARTIFACT_KIND[artifact.artifact_kind]
            or re.fullmatch(
                r"[0-9a-f]{64}", str(artifact.runtime_manifest_sha256 or "")
            )
            is None
            or re.fullmatch(r"[0-9a-f]{64}", str(artifact.request_sha256 or "")) is None
            or (artifact.input_binding_sha256 is None)
            != (artifact.input_binding_version is None)
        ):
            await self.db.rollback()
            return None
        try:
            expected_effective_request_sha256 = canonical_effective_render_request_hash(
                request_sha256=artifact.request_sha256,
                input_binding_sha256=artifact.input_binding_sha256,
                input_binding_version=artifact.input_binding_version,
            )
            geometry_manifest = StudioGeometryManifest.model_validate(
                artifact.geometry_manifest
            )
        except (ValidationError, TypeError, ValueError):
            await self.db.rollback()
            return None
        if (
            artifact.effective_request_sha256 != expected_effective_request_sha256
            or geometry_manifest.artifact_page_count != artifact.artifact_page_count
            or geometry_manifest.document_page_count != artifact.document_page_count
            or geometry_manifest.sha256 != artifact.geometry_manifest_sha256
            or not _geometry_matches_request(
                geometry_manifest,
                artifact_kind=artifact.artifact_kind,
                requested_page_number=artifact.requested_page_number,
            )
        ):
            await self.db.rollback()
            return None
        cached = StudioCachedOutput(
            object_ref=StudioObjectRef(
                tenant_id=artifact.tenant_id,
                object_key=artifact.object_key,
                sha256=artifact.content_sha256,
                byte_size=artifact.byte_size,
                media_type=artifact.media_type,
            ),
            artifact_kind=artifact.artifact_kind,
            effective_request_sha256=artifact.effective_request_sha256,
            runtime_manifest_sha256=artifact.runtime_manifest_sha256,
            artifact_page_count=artifact.artifact_page_count,
            document_page_count=artifact.document_page_count,
            geometry_manifest=geometry_manifest,
            geometry_manifest_sha256=artifact.geometry_manifest_sha256,
        )
        try:
            await asyncio.to_thread(
                object_store.read,
                cached.object_ref,
                max_bytes=max_bytes,
            )
        except Exception:
            await self.db.rollback()
            return None
        fresh = await self._clock_now()
        if (
            artifact.storage_state != "active"
            or artifact.delete_requested_at is not None
            or artifact.deleted_at is not None
            or (
                artifact.content_expires_at is not None
                and artifact.content_expires_at <= fresh
            )
            or artifact.cache_key != cache_key
            or artifact.object_key != expected_object_key
            or artifact.object_key != cached.object_ref.object_key
            or artifact.content_sha256 != cached.object_ref.sha256
            or artifact.byte_size != cached.object_ref.byte_size
            or artifact.media_type != cached.object_ref.media_type
            or artifact.artifact_kind != cached.artifact_kind
            or artifact.runtime_manifest_sha256 != cached.runtime_manifest_sha256
            or artifact.artifact_page_count != cached.artifact_page_count
            or artifact.document_page_count != cached.document_page_count
            or artifact.geometry_manifest_sha256 != cached.geometry_manifest_sha256
        ):
            await self.db.rollback()
            return None
        # Cache lookup owns no caller mutation. Release the row/advisory locks
        # before bytes are handed to the renderer.
        await self.db.rollback()
        return cached


class StudioRenderJobService:
    """Phase-4-facing actor-bound, transaction-neutral consumer facade."""

    __slots__ = ("_store",)

    def __init__(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        active_job_limit: int = 4,
        job_ttl: timedelta = timedelta(hours=24),
        renderer_manifest: StudioRendererManifest | None = None,
        renderer_manifests: Mapping[object, StudioRendererManifest] | None = None,
        input_binding_resolver: StudioInputBindingResolver | None = None,
        enqueue_rate_limit: int = 20,
        enqueue_rate_window: timedelta = timedelta(minutes=1),
        queued_byte_limit: int = 500 * 1024 * 1024,
        max_input_binding_bytes: int = 25 * 1024 * 1024,
        max_download_bytes: int = 25 * 1024 * 1024,
        retained_artifact_limit: int = 500,
        retained_byte_limit: int = 10 * 1024**3,
        live_artifact_limit: int = 1_000,
        live_byte_limit: int = 20 * 1024**3,
    ):
        self._store = _StudioRenderJobStore(
            db,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            active_job_limit=active_job_limit,
            job_ttl=job_ttl,
            renderer_manifest=renderer_manifest,
            renderer_manifests=renderer_manifests,
            input_binding_resolver=input_binding_resolver,
            enqueue_rate_limit=enqueue_rate_limit,
            enqueue_rate_window=enqueue_rate_window,
            queued_byte_limit=queued_byte_limit,
            max_input_binding_bytes=max_input_binding_bytes,
            max_download_bytes=max_download_bytes,
            retained_artifact_limit=retained_artifact_limit,
            retained_byte_limit=retained_byte_limit,
            live_artifact_limit=live_artifact_limit,
            live_byte_limit=live_byte_limit,
        )

    async def enqueue(
        self,
        request: StudioRenderRequest,
        *,
        idempotency_key: str,
        audit: StudioConsumerAudit,
        status_base_url: str = _CANONICAL_STATUS_BASE,
    ) -> StudioRenderAccepted:
        if audit is None or not callable(audit):
            raise StudioRenderServiceError(
                503,
                "audit_unavailable",
                "Studio auditing is temporarily unavailable.",
            )
        return await self._store.enqueue(
            request,
            idempotency_key=idempotency_key,
            status_base_url=status_base_url,
            audit=audit,
        )

    async def enqueue_test_render(
        self,
        request: StudioRenderRequest,
        *,
        idempotency_key: str,
        audit: StudioConsumerAudit,
        status_base_url: str = _CANONICAL_STATUS_BASE,
    ) -> StudioRenderAccepted:
        if audit is None or not callable(audit):
            raise StudioRenderServiceError(
                503,
                "audit_unavailable",
                "Studio auditing is temporarily unavailable.",
            )
        return await self._store.enqueue_test_render(
            request,
            idempotency_key=idempotency_key,
            status_base_url=status_base_url,
            audit=audit,
        )

    async def status(self, job_id: uuid.UUID) -> StudioRenderJobStatus:
        return await self._store.status(job_id)

    async def artifact_result(self, artifact_id: uuid.UUID) -> StudioRenderJobStatus:
        return await self._store.artifact_result(artifact_id)

    async def artifact_geometry(self, artifact_id: uuid.UUID) -> StudioArtifactGeometry:
        return await self._store.artifact_geometry(artifact_id)

    async def artifact_content(
        self,
        artifact_id: uuid.UUID,
        *,
        object_store: StudioObjectStore,
        max_bytes: int,
    ) -> StudioRenderArtifactContent:
        return await self._store.artifact_content(
            artifact_id,
            object_store=object_store,
            max_bytes=max_bytes,
        )

    async def request_cancel(
        self,
        job_id: uuid.UUID,
        *,
        audit: StudioConsumerAudit,
    ) -> StudioRenderJobStatus:
        if audit is None or not callable(audit):
            raise StudioRenderServiceError(
                503,
                "audit_unavailable",
                "Studio auditing is temporarily unavailable.",
            )
        return await self._store.request_cancel(job_id, audit=audit)


class StudioRenderWorkerService:
    """Worker-only lease/cache/adoption facade with no consumer operations."""

    __slots__ = ("_store",)

    def __init__(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        input_binding_resolver: StudioInputBindingResolver | None = None,
        retained_artifact_limit: int = 500,
        retained_byte_limit: int = 10 * 1024**3,
        live_artifact_limit: int = 1_000,
        live_byte_limit: int = 20 * 1024**3,
    ):
        self._store = _StudioRenderJobStore(
            db,
            tenant_id=tenant_id,
            input_binding_resolver=input_binding_resolver,
            retained_artifact_limit=retained_artifact_limit,
            retained_byte_limit=retained_byte_limit,
            live_artifact_limit=live_artifact_limit,
            live_byte_limit=live_byte_limit,
        )

    @staticmethod
    def _owns(row: DurableJob | None, lease: StudioJobLease) -> bool:
        return _StudioRenderJobStore._owns(row, lease)

    @staticmethod
    def _owns_live(
        row: DurableJob | None,
        lease: StudioJobLease,
        *,
        now: datetime,
    ) -> bool:
        return _StudioRenderJobStore._owns_live(row, lease, now=now)

    async def claim(
        self,
        job_id: uuid.UUID,
        *,
        owner: str,
        lease_seconds: int = 900,
    ) -> StudioJobLease | None:
        return await self._store.claim(job_id, owner=owner, lease_seconds=lease_seconds)

    async def renew_lease(self, lease: StudioJobLease) -> bool:
        return await self._store.renew_lease(lease)

    async def update_progress(self, lease: StudioJobLease, progress: int) -> bool:
        return await self._store.update_progress(lease, progress)

    async def fail_owned_job(
        self, lease: StudioJobLease, code: str, *, retryable: bool
    ) -> bool:
        await self._store._bind_tenant_context()
        return await self._store.fail_owned_job(lease, code, retryable=retryable)

    async def adopt_output(
        self,
        lease: StudioJobLease,
        output: StudioObjectRef,
        *,
        object_store: StudioObjectStore,
        artifact_kind: str,
        runtime_manifest_sha256: str,
        retention_class: str = "review",
        artifact_ttl_seconds: int,
        artifact_page_count: int,
        document_page_count: int,
        geometry_manifest: StudioGeometryManifest,
        geometry_manifest_sha256: str,
        metadata_ttl_seconds: int = 2_592_000,
    ) -> tuple[uuid.UUID, str]:
        await self._store._bind_tenant_context()
        return await self._store.adopt_output(
            lease,
            output,
            object_store=object_store,
            artifact_kind=artifact_kind,
            runtime_manifest_sha256=runtime_manifest_sha256,
            retention_class=retention_class,
            artifact_ttl_seconds=artifact_ttl_seconds,
            artifact_page_count=artifact_page_count,
            document_page_count=document_page_count,
            geometry_manifest=geometry_manifest,
            geometry_manifest_sha256=geometry_manifest_sha256,
            metadata_ttl_seconds=metadata_ttl_seconds,
        )

    async def find_cached_output(
        self,
        cache_key: str,
        *,
        object_store: StudioObjectStore,
        max_bytes: int,
    ) -> StudioCachedOutput | None:
        await self._store._bind_tenant_context()
        return await self._store.find_cached_output(
            cache_key,
            object_store=object_store,
            max_bytes=max_bytes,
        )

    async def stage_and_adopt_output(
        self,
        lease: StudioJobLease,
        content: bytes,
        *,
        object_store: StudioObjectStore,
        media_type: str,
        content_sha256: str,
        artifact_kind: str,
        runtime_manifest_sha256: str,
        retention_class: str = "review",
        artifact_ttl_seconds: int,
        artifact_page_count: int,
        document_page_count: int,
        geometry_manifest: StudioGeometryManifest,
        geometry_manifest_sha256: str,
        metadata_ttl_seconds: int = 2_592_000,
    ) -> tuple[uuid.UUID, str, StudioStagedObject]:
        await self._store._bind_tenant_context()
        return await self._store.stage_and_adopt_output(
            lease,
            content,
            object_store=object_store,
            media_type=media_type,
            content_sha256=content_sha256,
            artifact_kind=artifact_kind,
            runtime_manifest_sha256=runtime_manifest_sha256,
            retention_class=retention_class,
            artifact_ttl_seconds=artifact_ttl_seconds,
            artifact_page_count=artifact_page_count,
            document_page_count=document_page_count,
            geometry_manifest=geometry_manifest,
            geometry_manifest_sha256=geometry_manifest_sha256,
            metadata_ttl_seconds=metadata_ttl_seconds,
        )


def default_worker_owner() -> str:
    return f"studio-render:{socket.gethostname()}:{uuid.uuid4()}"
