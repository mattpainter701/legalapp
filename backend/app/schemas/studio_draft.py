"""Bounded API contracts for the server-side Template Studio foundation."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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


class StudioFieldInput(BaseModel):
    id: uuid.UUID | None = None
    automation_key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    label: str = Field(min_length=1, max_length=300)
    field_type: str = Field(min_length=1, max_length=40)
    required: bool = False
    position: int = Field(default=0, ge=0, le=9999)
    definition: dict[str, Any] = Field(default_factory=dict)


class StudioPlacementInput(BaseModel):
    id: uuid.UUID | None = None
    field_id: uuid.UUID
    format: StudioFormat
    anchor_kind: str = Field(min_length=1, max_length=40)
    anchor: dict[str, Any]


class StudioDraftCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    format: StudioFormat
    source_artifact_id: uuid.UUID | None = None
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_media_type: str = Field(min_length=1, max_length=100)
    published_template_id: uuid.UUID | None = None
    fields: list[StudioFieldInput] = Field(default_factory=list, max_length=200)
    placements: list[StudioPlacementInput] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def placement_fields_exist(self):
        supplied_ids = [item.id for item in self.fields if item.id is not None]
        supplied = set(supplied_ids)
        if len(supplied) != len(supplied_ids):
            raise ValueError("explicit field IDs must be unique")
        keys = [item.automation_key for item in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError("automation keys must be unique within a draft")
        placement_ids = [item.id for item in self.placements if item.id is not None]
        if len(set(placement_ids)) != len(placement_ids):
            raise ValueError("explicit placement IDs must be unique")
        if any(item.field_id not in supplied for item in self.placements):
            raise ValueError("placements require explicit field IDs from the same request")
        return self


class StudioDraftImport(BaseModel):
    """Create from the current published compatibility record."""

    template_id: uuid.UUID
    title: str | None = Field(default=None, min_length=1, max_length=300)


class StudioOperation(BaseModel):
    op: StudioOperationName
    field: StudioFieldInput | None = None
    field_id: uuid.UUID | None = None
    placement: StudioPlacementInput | None = None
    placement_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    source_artifact_id: uuid.UUID | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_media_type: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_shape(self):
        required = {
            "upsert_field": self.field is not None,
            "remove_field": self.field_id is not None,
            "upsert_placement": self.placement is not None,
            "remove_placement": self.placement_id is not None,
            "replace_source": all(
                (self.source_artifact_id, self.source_sha256, self.source_media_type)
            ),
            "set_metadata": self.title is not None,
        }
        if self.op in required and not required[self.op]:
            raise ValueError(f"operation {self.op} is missing its required payload")
        return self


class StudioDraftPatch(BaseModel):
    base_revision: int = Field(ge=1)
    operations: list[StudioOperation] = Field(min_length=1, max_length=100)


class StudioRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class StudioPromoteRequest(StudioRevisionRequest):
    status: Literal["draft", "active"] = "draft"


class StudioSourceContract(BaseModel):
    contract_version: Literal[1] = 1
    artifact_id: uuid.UUID
    sha256: str
    media_type: str


class StudioFieldResponse(BaseModel):
    id: uuid.UUID
    automation_key: str
    label: str
    field_type: str
    required: bool
    position: int
    definition: dict[str, Any]


class StudioPlacementResponse(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID
    format: str
    anchor_kind: str
    anchor: dict[str, Any]


class StudioDraftResponse(BaseModel):
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


class StudioSnapshotResponse(BaseModel):
    id: uuid.UUID
    draft_id: uuid.UUID
    revision: int
    identity_sha256: str
    content_sha256: str
    payload: dict[str, Any]


class StudioValidationResponse(BaseModel):
    draft_id: uuid.UUID
    revision: int
    identity_sha256: str
    valid: bool
    issues: list[dict[str, str]]


class StudioConflictDetail(BaseModel):
    code: Literal["stale_revision", "idempotency_key_mismatch"]
    message: str
    expected_revision: int | None = None
    current_revision: int | None = None
    current_etag: str | None = None
