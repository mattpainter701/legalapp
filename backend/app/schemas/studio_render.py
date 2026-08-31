"""Public, ORM-independent contracts for durable Template Studio rendering."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        pattern=r"^/api/[A-Za-z0-9_./-]+$",
    )
    expires_at: datetime


class StudioRenderJobStatus(StrictModel):
    """Tenant-authorized status without ORM or object-storage details."""

    job_id: uuid.UUID
    kind: StudioRenderJobKind
    state: StudioJobState
    progress: int = Field(ge=0, le=100)
    draft_id: uuid.UUID
    rendered_revision: int = Field(ge=1)
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: uuid.UUID
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: StudioRenderSourceContract
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_identity: str = Field(min_length=1, max_length=200)
    converter_identity: str = Field(min_length=1, max_length=200)
    validator_identity: str = Field(min_length=1, max_length=200)
    artifact_id: uuid.UUID | None = None
    adoption_outcome: StudioAdoptionOutcome | None = None
    error_code: StudioPublicErrorCode | None = None
    error_message: str | None = Field(default=None, max_length=300)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_state_shape(self):
        materialized = self.artifact_id is not None or self.adoption_outcome is not None
        if self.state == "completed":
            if self.artifact_id is None or self.adoption_outcome is None:
                raise ValueError("completed jobs require materialized artifact evidence")
        elif materialized:
            raise ValueError("artifact evidence exists only after materialization")
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
