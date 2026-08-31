"""Public, ORM-independent contracts for durable Template Studio rendering."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


StudioRenderJobKind = Literal[
    "studio_template_analysis",
    "studio_template_ocr",
    "studio_page_preview",
    "studio_test_render",
]
StudioArtifactKind = Literal["analysis", "ocr", "page_preview", "test_render"]
StudioJobState = Literal[
    "pending",
    "running",
    "retry_wait",
    "cancel_requested",
    "cancelled",
    "completed",
    "failed",
]
StudioAdoptionOutcome = Literal[
    "current_evidence", "stale_output", "cancelled_output"
]
StudioArtifactAvailability = Literal["available", "expired"]
StudioArtifactMetadataAvailability = Literal["available", "expired"]
StudioJobFailureCode = Literal[
    "cancelled",
    "expired",
    "hostile_input",
    "input_too_large",
    "job_data_unavailable",
    "output_too_large",
    "processor_timeout",
    "processor_unavailable",
    "source_integrity_failed",
    "storage_integrity_failed",
    "validation_failed",
]
StudioPublicErrorCode = Literal[
    "actor_mismatch",
    "artifact_expired",
    "artifact_not_found",
    "audit_unavailable",
    "cancelled",
    "expired",
    "hostile_input",
    "idempotency_key_expired",
    "idempotency_key_mismatch",
    "input_too_large",
    "invalid_idempotency_key",
    "invalid_job_kind",
    "invalid_job_transition",
    "invalid_status_resource",
    "job_data_unavailable",
    "job_not_found",
    "lease_lost",
    "output_too_large",
    "processor_timeout",
    "processor_unavailable",
    "revision_not_found",
    "source_integrity_failed",
    "stale_revision",
    "storage_integrity_failed",
    "studio_job_quota",
    "studio_job_rate",
    "studio_queued_bytes",
    "validation_failed",
]

STUDIO_PUBLIC_FAILURES = {
    "cancelled": "Studio processing was cancelled.",
    "expired": "The Studio processing request expired.",
    "hostile_input": "The source document is not safe to process.",
    "input_too_large": "The source document exceeds a processing limit.",
    "job_data_unavailable": "The Studio processing record is unavailable.",
    "output_too_large": "The rendered output exceeds a processing limit.",
    "processor_timeout": "Studio processing exceeded its time limit.",
    "processor_unavailable": "Studio processing is temporarily unavailable.",
    "source_integrity_failed": "The source document failed its integrity check.",
    "storage_integrity_failed": "The rendered output failed its integrity check.",
    "validation_failed": "The Studio revision is not valid for processing.",
}

STUDIO_PUBLIC_ERROR_MESSAGES = {
    **STUDIO_PUBLIC_FAILURES,
    "actor_mismatch": "Studio actor binding is invalid.",
    "artifact_expired": "The Studio artifact has expired.",
    "artifact_not_found": "Studio artifact not found.",
    "audit_unavailable": "Studio auditing is temporarily unavailable.",
    "idempotency_key_expired": "Idempotency-Key refers to an expired Studio request.",
    "idempotency_key_mismatch": (
        "Idempotency-Key was already used for another render request."
    ),
    "invalid_idempotency_key": (
        "Idempotency-Key must be 8-200 printable characters."
    ),
    "invalid_job_kind": "The Studio render job kind is invalid.",
    "invalid_job_transition": "Studio job state changed before this operation completed.",
    "invalid_status_resource": "Studio status resource is unavailable.",
    "job_not_found": "Studio job not found.",
    "lease_lost": "Studio job lease is no longer owned.",
    "revision_not_found": "Studio revision not found.",
    "stale_revision": (
        "Studio revision or source changed before processing was queued."
    ),
    "studio_job_quota": "The tenant Studio processing limit is reached.",
    "studio_job_rate": "The tenant Studio submission rate is reached.",
    "studio_queued_bytes": "The tenant Studio queued-byte limit is reached.",
}

STUDIO_PUBLIC_ERROR_STATUS = {
    "actor_mismatch": 403,
    "artifact_expired": 410,
    "artifact_not_found": 404,
    "audit_unavailable": 503,
    "cancelled": 409,
    "expired": 409,
    "hostile_input": 422,
    "idempotency_key_expired": 409,
    "idempotency_key_mismatch": 409,
    "input_too_large": 413,
    "invalid_idempotency_key": 422,
    "invalid_job_kind": 422,
    "invalid_job_transition": 409,
    "invalid_status_resource": 500,
    "job_data_unavailable": 409,
    "job_not_found": 404,
    "lease_lost": 409,
    "output_too_large": 413,
    "processor_timeout": 504,
    "processor_unavailable": 503,
    "revision_not_found": 404,
    "source_integrity_failed": 409,
    "stale_revision": 409,
    "storage_integrity_failed": 409,
    "studio_job_quota": 429,
    "studio_job_rate": 429,
    "studio_queued_bytes": 429,
    "validation_failed": 409,
}

STUDIO_PUBLIC_ERROR_RETRYABLE = {
    code: code
    in {
        "audit_unavailable",
        "processor_timeout",
        "processor_unavailable",
        "studio_job_quota",
        "studio_job_rate",
        "studio_queued_bytes",
    }
    for code in STUDIO_PUBLIC_ERROR_MESSAGES
}

STUDIO_PUBLIC_ERROR_DETAIL_KEYS = {
    "stale_revision": frozenset({"current_revision", "current_etag"}),
}

STUDIO_RENDER_JOB_KINDS = frozenset(
    {
        "studio_template_analysis",
        "studio_template_ocr",
        "studio_page_preview",
        "studio_test_render",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def canonical_json_sha256(value: Any) -> str:
    """Hash a finite, deterministic JSON representation."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class StudioRenderSourceContract(StrictModel):
    """Complete Phase 2 source contract v1, copied as a stable public boundary."""

    contract_version: Literal[1] = 1
    artifact_id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=100)
    format: Literal["markdown", "docx", "pdf"]

    @model_validator(mode="after")
    def validate_media_type(self):
        expected = {
            "markdown": "text/markdown",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pdf": "application/pdf",
        }[self.format]
        if self.media_type != expected:
            raise ValueError("source format and media type do not match")
        return self


class StudioRenderOptions(StrictModel):
    """Bounded renderer controls; document/test values are intentionally absent."""

    flatten_pdf: bool = False
    preview_purpose: Literal["editor", "test", "validation"] = "test"
    page_number: int | None = Field(default=None, ge=1, le=10_000)
    max_pages: int = Field(default=250, ge=1, le=1_000)
    max_output_bytes: int = Field(default=25 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)

    @model_validator(mode="after")
    def finite_and_bounded(self):
        if self.page_number is not None and self.page_number > self.max_pages:
            raise ValueError("page_number cannot exceed max_pages")
        values = self.model_dump(mode="json")
        if any(isinstance(value, float) and not math.isfinite(value) for value in values.values()):
            raise ValueError("render options must be finite")
        if len(json.dumps(values, separators=(",", ":"), allow_nan=False)) > 2048:
            raise ValueError("render options exceed their bounded contract")
        return self

    @property
    def sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class StudioPageGeometry(StrictModel):
    """Normalized geometry for one source-document page."""

    page_number: int = Field(ge=1, le=10_000)
    coordinate_space: Literal["none", "points", "pixels"]
    width_points: float | None = Field(default=None, gt=0, le=100_000_000)
    height_points: float | None = Field(default=None, gt=0, le=100_000_000)
    width_px: int | None = Field(default=None, ge=1, le=100_000_000)
    height_px: int | None = Field(default=None, ge=1, le=100_000_000)
    dpi_x: float | None = Field(default=None, gt=0, le=100_000_000)
    dpi_y: float | None = Field(default=None, gt=0, le=100_000_000)

    @model_validator(mode="after")
    def validate_coordinate_space(self):
        points = (self.width_points, self.height_points)
        pixels = (self.width_px, self.height_px, self.dpi_x, self.dpi_y)
        if self.coordinate_space == "none" and any(
            value is not None for value in (*points, *pixels)
        ):
            raise ValueError("geometry-free pages cannot carry dimensions")
        if self.coordinate_space == "points" and (
            any(value is None for value in points)
            or any(value is not None for value in pixels)
        ):
            raise ValueError("point geometry is incomplete")
        if self.coordinate_space == "pixels" and (
            any(value is None for value in pixels)
            or any(value is not None for value in points)
        ):
            raise ValueError("pixel geometry is incomplete")
        for value in (*points, *pixels):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("page geometry must be finite")
        return self


class StudioGeometryManifest(StrictModel):
    """Hashable page evidence independent of artifact byte representation."""

    contract_version: Literal[1] = 1
    artifact_page_count: int = Field(ge=1, le=1_000)
    document_page_count: int = Field(ge=1, le=10_000)
    pages: list[StudioPageGeometry] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_pages(self):
        if len(self.pages) != self.artifact_page_count:
            raise ValueError("artifact page count does not match geometry")
        page_numbers = [page.page_number for page in self.pages]
        if len(set(page_numbers)) != len(page_numbers):
            raise ValueError("page geometry contains duplicates")
        if page_numbers != sorted(page_numbers):
            raise ValueError("page geometry is not ordered")
        if page_numbers[-1] > self.document_page_count:
            raise ValueError("page geometry exceeds the source document")
        return self

    @property
    def sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class StudioRendererComponent(StrictModel):
    """Pinned, non-secret identity for one executable render dependency."""

    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    version: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.+-]+$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StudioRendererManifest(StrictModel):
    """Canonical server-owned identity participating in cache and evidence."""

    contract_version: Literal[1] = 1
    isolation_policy_id: str = Field(
        min_length=3,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]+$",
    )
    boundary_kind: Literal["attested_supervisor_v1"] = "attested_supervisor_v1"
    launcher_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sandbox_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixed_arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    font_pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer: StudioRendererComponent
    rasterizer: StudioRendererComponent
    converter: StudioRendererComponent
    validator: StudioRendererComponent

    @property
    def sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


def canonical_render_request_hash(
    *,
    kind: StudioRenderJobKind,
    draft_id: uuid.UUID,
    expected_revision: int,
    identity_sha256: str,
    snapshot_id: uuid.UUID,
    content_sha256: str,
    source: StudioRenderSourceContract,
    render_options: StudioRenderOptions,
    requested_by: uuid.UUID,
    input_binding_id: uuid.UUID | None,
) -> str:
    return canonical_json_sha256(
        {
            "kind": kind,
            "draft_id": str(draft_id),
            "expected_revision": expected_revision,
            "identity_sha256": identity_sha256,
            "snapshot_id": str(snapshot_id),
            "content_sha256": content_sha256,
            "source": source.model_dump(mode="json"),
            "render_options": render_options.model_dump(mode="json"),
            "requested_by": str(requested_by),
            "input_binding_id": str(input_binding_id) if input_binding_id else None,
        }
    )


def canonical_effective_render_request_hash(
    *,
    request_sha256: str,
    input_binding_sha256: str | None,
    input_binding_version: int | None,
) -> str:
    """Bind admitted server-resolved inputs without changing client intent."""

    if (input_binding_sha256 is None) != (input_binding_version is None):
        raise ValueError("input binding identity is incomplete")
    if (
        not isinstance(request_sha256, str)
        or len(request_sha256) != 64
        or any(character not in "0123456789abcdef" for character in request_sha256)
    ):
        raise ValueError("invalid client request hash")
    if input_binding_sha256 is not None and (
        not isinstance(input_binding_sha256, str)
        or len(input_binding_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in input_binding_sha256
        )
    ):
        raise ValueError("invalid input binding hash")
    if input_binding_version is not None and not (
        isinstance(input_binding_version, int)
        and not isinstance(input_binding_version, bool)
        and 1 <= input_binding_version <= 2_147_483_647
    ):
        raise ValueError("invalid input binding version")
    return canonical_json_sha256(
        {
            "contract_version": 1,
            "request_sha256": request_sha256,
            "input_binding_sha256": input_binding_sha256,
            "input_binding_version": input_binding_version,
        }
    )


class StudioRenderRequest(StrictModel):
    """Phase 4 handoff. Raw document bytes and merge values cannot be supplied."""

    kind: StudioRenderJobKind = "studio_test_render"
    draft_id: uuid.UUID
    expected_revision: int = Field(ge=1)
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: uuid.UUID
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: StudioRenderSourceContract
    render_options: StudioRenderOptions = Field(default_factory=StudioRenderOptions)
    requested_by: uuid.UUID
    input_binding_id: uuid.UUID | None = None
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_request(self):
        if self.kind == "studio_page_preview" and self.render_options.page_number is None:
            raise ValueError("page preview jobs require page_number")
        if self.kind != "studio_page_preview" and self.render_options.page_number is not None:
            raise ValueError("page_number is valid only for page preview jobs")
        if self.input_binding_id is not None and self.kind != "studio_test_render":
            raise ValueError("input bindings are valid only for test renders")
        actual = canonical_render_request_hash(
            kind=self.kind,
            draft_id=self.draft_id,
            expected_revision=self.expected_revision,
            identity_sha256=self.identity_sha256,
            snapshot_id=self.snapshot_id,
            content_sha256=self.content_sha256,
            source=self.source,
            render_options=self.render_options,
            requested_by=self.requested_by,
            input_binding_id=self.input_binding_id,
        )
        if actual != self.request_sha256:
            raise ValueError("Studio render request hash mismatch")
        return self


class StudioRenderIntent(StrictModel):
    """Client intent; actor identity and request hash are server-owned."""

    kind: StudioRenderJobKind = "studio_test_render"
    draft_id: uuid.UUID
    expected_revision: int = Field(ge=1)
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: uuid.UUID
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: StudioRenderSourceContract
    render_options: StudioRenderOptions = Field(default_factory=StudioRenderOptions)
    input_binding_id: uuid.UUID | None = None

    def bind_actor(self, actor_user_id: uuid.UUID) -> StudioRenderRequest:
        request_sha256 = canonical_render_request_hash(
            kind=self.kind,
            draft_id=self.draft_id,
            expected_revision=self.expected_revision,
            identity_sha256=self.identity_sha256,
            snapshot_id=self.snapshot_id,
            content_sha256=self.content_sha256,
            source=self.source,
            render_options=self.render_options,
            requested_by=actor_user_id,
            input_binding_id=self.input_binding_id,
        )
        return StudioRenderRequest(
            **self.model_dump(),
            requested_by=actor_user_id,
            request_sha256=request_sha256,
        )


class StudioRenderAccepted(StrictModel):
    """202 response: only an opaque job handle and tenant-safe status resource."""

    job_id: uuid.UUID
    status_url: str = Field(
        min_length=1,
        max_length=500,
        pattern=r"^/api/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$",
    )
    job_expires_at: AwareDatetime


class StudioArtifactGeometry(StrictModel):
    """Authenticated, hash-verifiable page geometry for one artifact."""

    artifact_id: uuid.UUID
    geometry_manifest: StudioGeometryManifest
    geometry_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_manifest_hash(self):
        if self.geometry_manifest.sha256 != self.geometry_manifest_sha256:
            raise ValueError("geometry manifest hash mismatch")
        return self


class StudioRenderErrorDetails(StrictModel):
    current_revision: int | None = Field(default=None, ge=1)
    current_etag: str | None = Field(
        default=None,
        max_length=160,
        pattern=(
            r'^"studio:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
            r'[0-9a-f]{4}-[0-9a-f]{12}:[1-9][0-9]*:[0-9a-f]{64}"$'
        ),
    )


class StudioRenderPublicError(StrictModel):
    code: StudioPublicErrorCode
    message: str = Field(min_length=1, max_length=300)
    retryable: bool
    details: StudioRenderErrorDetails | None = None

    @model_validator(mode="after")
    def validate_canonical_error(self):
        if self.message != STUDIO_PUBLIC_ERROR_MESSAGES[self.code]:
            raise ValueError("Studio public error message is not canonical")
        if self.retryable != STUDIO_PUBLIC_ERROR_RETRYABLE[self.code]:
            raise ValueError("Studio public error retryability is not canonical")
        allowed = STUDIO_PUBLIC_ERROR_DETAIL_KEYS.get(self.code, frozenset())
        supplied = (
            set(self.details.model_dump(exclude_none=True))
            if self.details is not None
            else set()
        )
        if not supplied.issubset(allowed):
            raise ValueError("Studio public error details are not allowed")
        return self


class StudioRenderPublicErrorEnvelope(StrictModel):
    """FastAPI's explicit HTTPException response envelope."""

    detail: StudioRenderPublicError


class StudioRenderJobStatus(StrictModel):
    """Tenant-authorized status without ORM or object-storage details."""

    job_id: uuid.UUID
    status_url: str = Field(
        min_length=1,
        max_length=500,
        pattern=r"^/api/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$",
    )
    kind: StudioRenderJobKind
    state: StudioJobState
    progress: int = Field(ge=0, le=100)
    attempts: int = Field(ge=0, le=100)
    max_attempts: int = Field(ge=1, le=100)
    retry_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    leased_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
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
    renderer_manifest: StudioRendererManifest
    runtime_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_binding_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    input_binding_version: int | None = Field(default=None, ge=1)
    artifact_id: uuid.UUID | None = None
    artifact_availability: StudioArtifactAvailability | None = None
    artifact_metadata_availability: StudioArtifactMetadataAvailability | None = None
    result_url: str | None = Field(
        default=None,
        max_length=500,
        pattern=r"^/api/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$",
    )
    download_url: str | None = Field(
        default=None,
        max_length=500,
        pattern=r"^/api/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$",
    )
    geometry_url: str | None = Field(
        default=None,
        max_length=500,
        pattern=r"^/api/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$",
    )
    adoption_outcome: StudioAdoptionOutcome | None = None
    adopted_as_preferred_evidence: bool | None = None
    is_preferred_evidence: bool | None = None
    auto_open: bool = False
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_media_type: str | None = Field(default=None, min_length=1, max_length=100)
    output_byte_size: int | None = Field(default=None, ge=1, le=100 * 1024 * 1024)
    artifact_page_count: int | None = Field(default=None, ge=1, le=1_000)
    document_page_count: int | None = Field(default=None, ge=1, le=10_000)
    geometry_manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    retention_class: Literal["ephemeral", "review", "evidence"] | None = None
    error_code: StudioJobFailureCode | None = None
    error_message: str | None = Field(default=None, max_length=300)
    error_retryable: bool | None = None
    job_expires_at: AwareDatetime
    content_expires_at: AwareDatetime | None = None
    metadata_expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_state_shape(self):
        if self.render_options.sha256 != self.render_options_sha256:
            raise ValueError("render options hash mismatch")
        if self.effective_request_sha256 != canonical_effective_render_request_hash(
            request_sha256=self.request_sha256,
            input_binding_sha256=self.input_binding_sha256,
            input_binding_version=self.input_binding_version,
        ):
            raise ValueError("effective request hash mismatch")
        if self.renderer_manifest.sha256 != self.runtime_manifest_sha256:
            raise ValueError("renderer manifest hash mismatch")
        if (self.input_binding_sha256 is None) != (
            self.input_binding_version is None
        ):
            raise ValueError("input binding identity is incomplete")
        if self.attempts > self.max_attempts:
            raise ValueError("attempt count exceeds the retry limit")
        terminal = self.state in {"cancelled", "completed", "failed"}
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal state and completion timestamp disagree")
        if terminal != (self.progress == 100):
            raise ValueError("terminal state and progress disagree")
        if (self.state == "retry_wait") != (self.retry_at is not None):
            raise ValueError("retry timestamp is valid only for retry wait")
        if self.leased_at is not None and self.state not in {
            "running",
            "cancel_requested",
        }:
            raise ValueError("lease timestamp is valid only for an owned job")
        if self.state in {"running", "cancel_requested"} and self.leased_at is None:
            raise ValueError("owned jobs require a lease timestamp")
        materialized = self.artifact_id is not None or self.adoption_outcome is not None
        if self.state == "completed":
            if self.artifact_id is None or self.adoption_outcome is None:
                raise ValueError("completed jobs require materialized artifact evidence")
            required_metadata = (
                self.artifact_availability,
                self.artifact_metadata_availability,
                self.adopted_as_preferred_evidence,
                self.is_preferred_evidence,
                self.retention_class,
            )
            if any(value is None for value in required_metadata):
                raise ValueError("completed jobs require artifact metadata")
            if (
                self.retention_class in {"ephemeral", "review"}
                and (
                    self.content_expires_at is None
                    or self.metadata_expires_at is None
                    or self.metadata_expires_at <= self.content_expires_at
                )
            ):
                raise ValueError("temporary artifacts require ordered retention expiry")
            if (
                self.retention_class == "evidence"
                and (
                    self.content_expires_at is not None
                    or self.metadata_expires_at is not None
                )
            ):
                raise ValueError("evidence artifacts do not expire")
            if self.kind == "studio_page_preview" and self.artifact_page_count != 1:
                if self.artifact_metadata_availability == "available":
                    raise ValueError("page previews contain exactly one artifact page")
            metadata = (
                self.result_url,
                self.download_url,
                self.geometry_url,
                self.output_sha256,
                self.output_media_type,
                self.output_byte_size,
                self.artifact_page_count,
                self.document_page_count,
                self.geometry_manifest_sha256,
            )
            if self.artifact_metadata_availability == "available" and any(
                value is None for value in metadata
            ):
                raise ValueError("available artifact metadata is incomplete")
            if self.artifact_metadata_availability == "expired" and any(
                value is not None for value in metadata
            ):
                raise ValueError("expired artifact metadata must be redacted")
            if (
                self.artifact_metadata_availability == "expired"
                and self.artifact_availability != "expired"
            ):
                raise ValueError("metadata cannot expire before artifact content")
            if self.adopted_as_preferred_evidence != (
                self.adoption_outcome == "current_evidence"
            ):
                raise ValueError("adoption evidence state is inconsistent")
            if self.adoption_outcome in {"stale_output", "cancelled_output"} and (
                self.is_preferred_evidence is not False or self.auto_open
            ):
                raise ValueError("diagnostic output cannot be current or auto-opened")
            if self.auto_open and not (
                self.adoption_outcome == "current_evidence"
                and self.is_preferred_evidence is True
                and self.artifact_availability == "available"
                and self.artifact_metadata_availability == "available"
            ):
                raise ValueError("only live current evidence may auto-open")
            if self.artifact_availability == "expired" and self.auto_open:
                raise ValueError("expired artifacts cannot auto-open")
        elif materialized:
            raise ValueError("artifact evidence exists only after materialization")
        elif self.artifact_availability is not None:
            raise ValueError("artifact availability exists only after materialization")
        elif self.artifact_metadata_availability is not None:
            raise ValueError("artifact metadata availability exists only after materialization")
        elif self.content_expires_at is not None or self.metadata_expires_at is not None:
            raise ValueError("artifact expiry exists only after materialization")
        elif any(
            value is not None
            for value in (
                self.result_url,
                self.download_url,
                self.geometry_url,
                self.adopted_as_preferred_evidence,
                self.is_preferred_evidence,
                self.output_sha256,
                self.output_media_type,
                self.output_byte_size,
                self.artifact_page_count,
                self.document_page_count,
                self.geometry_manifest_sha256,
                self.retention_class,
            )
        ):
            raise ValueError("artifact metadata exists only after materialization")
        if self.state != "completed" and self.auto_open:
            raise ValueError("only completed current evidence may auto-open")
        if self.state == "failed":
            if (
                self.error_code is None
                or self.error_message is None
                or self.error_retryable is None
            ):
                raise ValueError("failed jobs require a sanitized failure")
            if self.error_message != STUDIO_PUBLIC_FAILURES[self.error_code]:
                raise ValueError("failed jobs require the canonical sanitized message")
            if self.error_retryable != STUDIO_PUBLIC_ERROR_RETRYABLE[self.error_code]:
                raise ValueError("failed jobs require canonical retryability")
        elif (
            self.error_code is not None
            or self.error_message is not None
            or self.error_retryable is not None
        ):
            raise ValueError("failure details exist only for failed jobs")
        return self


# Phase 4 originally named its handoff this way; keep the public alias ORM-free.
StudioTestRenderRequest = StudioRenderRequest
StudioJobAccepted = StudioRenderAccepted
StudioJobStatus = StudioRenderJobStatus
