"""Strict, bounded API contracts for the Template Studio foundation."""

from __future__ import annotations

import math
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


StudioFormat = Literal["markdown", "docx", "pdf"]
StudioOperationName = Literal[
    "set_metadata",
    "upsert_field",
    "remove_field",
    "upsert_placement",
    "remove_placement",
    "replace_source",
    "archive",
    "restore",
    "request_cancel",
    "clear_cancel",
]
AutomationKey = Annotated[
    str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
]
ClientKey = Annotated[
    str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
]
BoundedIdentifier = Annotated[
    str, Field(min_length=1, max_length=200, pattern=r"^[^\x00-\x1f\x7f]+$")
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarkdownTokenAnchor(StrictModel):
    token: AutomationKey


class PdfAcroFormAnchor(StrictModel):
    field_name: BoundedIdentifier


def _ordered_rect(value: tuple[float, float, float, float], field: str) -> None:
    if not all(math.isfinite(number) for number in value):
        raise ValueError(f"{field} coordinates must be finite")
    left, bottom, right, top = value
    if min(value) < 0 or max(value) > 20_000 or right <= left or top <= bottom:
        raise ValueError(f"{field} must be an ordered rectangle within page bounds")


class PdfOverlayAnchor(StrictModel):
    page: int = Field(ge=1, le=10_000)
    rect: tuple[float, float, float, float]
    source_rect: tuple[float, float, float, float] | None = None
    font_size: float | None = Field(default=None, gt=0, le=200)
    erase_source: bool = False
    source_kind: Literal["manual", "text", "ocr"] = "manual"

    @model_validator(mode="after")
    def validate_geometry(self):
        _ordered_rect(self.rect, "rect")
        if self.source_rect is not None:
            _ordered_rect(self.source_rect, "source_rect")
        if self.font_size is not None and not math.isfinite(self.font_size):
            raise ValueError("font_size must be finite")
        return self


class DocxSourceKeyAnchor(StrictModel):
    source_key: str = Field(pattern=r"^docx:[0-9a-f]{24}$")


class DocxSemanticAnchor(StrictModel):
    paragraph_ordinal: int = Field(ge=0, le=1_000_000)
    start: int = Field(ge=0, le=10_000_000)
    end: int = Field(gt=0, le=10_000_000)
    source_key: str | None = Field(default=None, pattern=r"^docx:[0-9a-f]{24}$")

    @model_validator(mode="after")
    def validate_range(self):
        if self.end <= self.start:
            raise ValueError("DOCX anchor end must be greater than start")
        return self


class DocxContentControlAnchor(StrictModel):
    tag: BoundedIdentifier


_ANCHOR_MODELS = {
    ("markdown", "template_token"): MarkdownTokenAnchor,
    ("pdf", "acroform_field"): PdfAcroFormAnchor,
    ("pdf", "overlay"): PdfOverlayAnchor,
    ("docx", "source_key"): DocxSourceKeyAnchor,
    ("docx", "semantic_anchor"): DocxSemanticAnchor,
    ("docx", "content_control"): DocxContentControlAnchor,
}


def canonical_placement_anchor(
    format_name: str, anchor_kind: str, anchor: dict[str, Any]
) -> dict[str, Any]:
    model_type = _ANCHOR_MODELS.get((format_name, anchor_kind))
    if model_type is None:
        raise ValueError("unsupported placement format/kind combination")
    return model_type.model_validate(anchor).model_dump(exclude_none=True)


class StudioFieldCreateInput(StrictModel):
    client_key: ClientKey
    automation_key: AutomationKey
    label: str = Field(min_length=1, max_length=300)
    field_type: str = Field(min_length=1, max_length=40)
    required: bool = False
    position: int = Field(default=0, ge=0, le=9999)
    definition: dict[str, Any] = Field(default_factory=dict)


class StudioFieldPatchInput(StrictModel):
    id: uuid.UUID | None = None
    automation_key: AutomationKey
    label: str = Field(min_length=1, max_length=300)
    field_type: str = Field(min_length=1, max_length=40)
    required: bool = False
    position: int = Field(default=0, ge=0, le=9999)
    definition: dict[str, Any] = Field(default_factory=dict)


class StudioPlacementCreateInput(StrictModel):
    client_key: ClientKey
    field_client_key: ClientKey
    format: StudioFormat
    anchor_kind: str = Field(min_length=1, max_length=40)
    anchor: dict[str, Any]

    @model_validator(mode="after")
    def canonicalize_anchor(self):
        self.anchor = canonical_placement_anchor(
            self.format, self.anchor_kind, self.anchor
        )
        return self


class StudioPlacementPatchInput(StrictModel):
    id: uuid.UUID | None = None
    field_id: uuid.UUID
    format: StudioFormat
    anchor_kind: str = Field(min_length=1, max_length=40)
    anchor: dict[str, Any]

    @model_validator(mode="after")
    def canonicalize_anchor(self):
        self.anchor = canonical_placement_anchor(
            self.format, self.anchor_kind, self.anchor
        )
        return self


class StudioDraftCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    format: StudioFormat
    source_artifact_id: uuid.UUID
    fields: list[StudioFieldCreateInput] = Field(default_factory=list, max_length=200)
    placements: list[StudioPlacementCreateInput] = Field(
        default_factory=list, max_length=1000
    )

    @model_validator(mode="after")
    def validate_local_correlations(self):
        field_keys = [item.client_key for item in self.fields]
        if len(set(field_keys)) != len(field_keys):
            raise ValueError("field client keys must be unique")
        automation_keys = [item.automation_key for item in self.fields]
        if len(set(automation_keys)) != len(automation_keys):
            raise ValueError("automation keys must be unique within a draft")
        placement_keys = [item.client_key for item in self.placements]
        if len(set(placement_keys)) != len(placement_keys):
            raise ValueError("placement client keys must be unique")
        known_fields = set(field_keys)
        if any(item.field_client_key not in known_fields for item in self.placements):
            raise ValueError(
                "placements must reference a field client key in this request"
            )
        if any(item.format != self.format for item in self.placements):
            raise ValueError("placement format must match the draft format")
        return self


class StudioDraftImport(StrictModel):
    template_id: uuid.UUID
    title: str | None = Field(default=None, min_length=1, max_length=300)


class StudioOperation(StrictModel):
    op: StudioOperationName
    field: StudioFieldPatchInput | None = None
    field_id: uuid.UUID | None = None
    placement: StudioPlacementPatchInput | None = None
    placement_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    source_artifact_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        allowed = {
            "set_metadata": {"op", "title"},
            "upsert_field": {"op", "field"},
            "remove_field": {"op", "field_id"},
            "upsert_placement": {"op", "placement"},
            "remove_placement": {"op", "placement_id"},
            "replace_source": {"op", "source_artifact_id"},
            "archive": {"op"},
            "restore": {"op"},
            "request_cancel": {"op"},
            "clear_cancel": {"op"},
        }[self.op]
        if self.model_fields_set != allowed:
            raise ValueError(f"operation {self.op} has missing or extraneous payload")
        return self


class StudioDraftPatch(StrictModel):
    base_revision: int = Field(ge=1)
    operations: list[StudioOperation] = Field(min_length=1, max_length=100)


class StudioRevisionRequest(StrictModel):
    expected_revision: int = Field(ge=1)


class StudioPromoteRequest(StudioRevisionRequest):
    status: Literal["draft"] = "draft"


class StudioSourceContract(StrictModel):
    contract_version: Literal[1] = 1
    artifact_id: uuid.UUID
    sha256: str
    media_type: str


class StudioFieldResponse(StrictModel):
    id: uuid.UUID
    automation_key: str
    label: str
    field_type: str
    required: bool
    position: int
    definition: dict[str, Any]


class StudioPlacementResponse(StrictModel):
    id: uuid.UUID
    field_id: uuid.UUID
    format: str
    anchor_kind: str
    anchor: dict[str, Any]


class StudioDraftResponse(StrictModel):
    id: uuid.UUID
    title: str
    format: str
    lifecycle_state: str
    revision: int
    identity_sha256: str
    published_template_id: uuid.UUID | None
    source: StudioSourceContract
    fields: list[StudioFieldResponse]
    placements: list[StudioPlacementResponse]
    evidence_revision: int | None
    evidence_invalidated: bool
    cancellation_requested: bool
    etag: str


class StudioSnapshotResponse(StrictModel):
    id: uuid.UUID
    draft_id: uuid.UUID
    revision: int
    identity_sha256: str
    content_sha256: str
    payload: dict[str, Any]


class StudioValidationResponse(StrictModel):
    draft_id: uuid.UUID
    revision: int
    identity_sha256: str
    valid: bool
    issues: list[dict[str, str]]


class StudioConflictDetail(StrictModel):
    code: Literal[
        "stale_revision", "idempotency_key_mismatch", "stale_published_template"
    ]
    message: str
    expected_revision: int | None = None
    current_revision: int | None = None
    current_etag: str | None = None
