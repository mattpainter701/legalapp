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
StudioPublicErrorCode = Literal[
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
        values = self.model_dump(mode="json")
        if any(isinstance(value, float) and not math.isfinite(value) for value in values.values()):
            raise ValueError("render options must be finite")
        if len(json.dumps(values, separators=(",", ":"), allow_nan=False)) > 2048:
            raise ValueError("render options exceed their bounded contract")
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


class StudioRenderAccepted(StrictModel):
    """202 response: only an opaque job handle and tenant-safe status resource."""

    job_id: uuid.UUID
    status_url: str = Field(
        min_length=1,
        max_length=500,
        pattern=r"^/api/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$",
    )
    job_expires_at: AwareDatetime


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
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]+$")
    message: str = Field(min_length=1, max_length=300)
    details: StudioRenderErrorDetails | None = None


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
    renderer_manifest: StudioRendererManifest
    runtime_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_binding_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    input_binding_version: int | None = Field(default=None, ge=1)
    artifact_id: uuid.UUID | None = None
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
    adoption_outcome: StudioAdoptionOutcome | None = None
    adopted_as_current_evidence: bool | None = None
    is_current_evidence: bool | None = None
    auto_open: bool = False
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output_media_type: str | None = Field(default=None, min_length=1, max_length=100)
    output_byte_size: int | None = Field(default=None, ge=1, le=100 * 1024 * 1024)
    page_count: int | None = Field(default=None, ge=1, le=10_000)
    mapping_manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    retention_class: Literal["ephemeral", "review", "evidence"] | None = None
    error_code: StudioPublicErrorCode | None = None
    error_message: str | None = Field(default=None, max_length=300)
    job_expires_at: AwareDatetime
    artifact_expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_state_shape(self):
        if self.render_options.sha256 != self.render_options_sha256:
            raise ValueError("render options hash mismatch")
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
                self.result_url,
                self.download_url,
                self.adopted_as_current_evidence,
                self.is_current_evidence,
                self.output_sha256,
                self.output_media_type,
                self.output_byte_size,
                self.page_count,
                self.retention_class,
            )
            if any(value is None for value in required_metadata):
                raise ValueError("completed jobs require artifact metadata")
            if (
                self.retention_class in {"ephemeral", "review"}
                and self.artifact_expires_at is None
            ):
                raise ValueError("temporary artifacts require expiry")
            if (
                self.retention_class == "evidence"
                and self.artifact_expires_at is not None
            ):
                raise ValueError("evidence artifacts do not expire")
            if self.kind == "studio_page_preview" and (
                self.mapping_manifest_sha256 is None
            ):
                raise ValueError("page previews require authoritative page metadata")
            if self.adopted_as_current_evidence != (
                self.adoption_outcome == "current_evidence"
            ):
                raise ValueError("adoption evidence state is inconsistent")
            if self.adoption_outcome in {"stale_output", "cancelled_output"} and (
                self.is_current_evidence is not False or self.auto_open
            ):
                raise ValueError("diagnostic output cannot be current or auto-opened")
            if self.auto_open and not (
                self.adoption_outcome == "current_evidence"
                and self.is_current_evidence is True
            ):
                raise ValueError("only live current evidence may auto-open")
        elif materialized:
            raise ValueError("artifact evidence exists only after materialization")
        elif self.artifact_expires_at is not None:
            raise ValueError("artifact expiry exists only after materialization")
        elif any(
            value is not None
            for value in (
                self.result_url,
                self.download_url,
                self.adopted_as_current_evidence,
                self.is_current_evidence,
                self.output_sha256,
                self.output_media_type,
                self.output_byte_size,
                self.page_count,
                self.mapping_manifest_sha256,
                self.retention_class,
            )
        ):
            raise ValueError("artifact metadata exists only after materialization")
        if self.state != "completed" and self.auto_open:
            raise ValueError("only completed current evidence may auto-open")
        if self.state == "failed":
            if self.error_code is None or self.error_message is None:
                raise ValueError("failed jobs require a sanitized failure")
            if self.error_message != STUDIO_PUBLIC_FAILURES[self.error_code]:
                raise ValueError("failed jobs require the canonical sanitized message")
        elif self.error_code is not None or self.error_message is not None:
            raise ValueError("failure details exist only for failed jobs")
        return self


# Phase 4 originally named its handoff this way; keep the public alias ORM-free.
StudioTestRenderRequest = StudioRenderRequest
StudioJobAccepted = StudioRenderAccepted
StudioJobStatus = StudioRenderJobStatus
