"""Validation contracts for the research workspace API."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

EvidenceClass = Literal["cited", "verify", "model"]
RecordType = Literal[
    "issue",
    "search",
    "folder",
    "authority",
    "highlight",
    "annotation",
    "exclusion",
    "outline",
    "memo",
    "alert",
]
WorkspaceRole = Literal["owner", "editor", "reviewer", "viewer"]
CurrentnessState = Literal[
    "unknown", "current", "stale", "review_needed", "unavailable"
]
TreatmentState = Literal[
    "unknown",
    "favorable",
    "negative",
    "neutral",
    "caution",
    "review_needed",
    "unavailable",
]


class WorkspaceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)

    @field_validator("title")
    @classmethod
    def title_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class MemberUpsert(BaseModel):
    user_id: uuid.UUID
    role: WorkspaceRole


class RecordCreate(BaseModel):
    record_type: RecordType
    title: str = Field(min_length=1, max_length=500)
    body: str | None = Field(default=None, max_length=200_000)
    evidence_class: EvidenceClass = "model"
    source_url: HttpUrl | None = None
    source_version: str | None = Field(default=None, max_length=200)
    source_as_of: datetime | None = None
    currentness_state: CurrentnessState = "unknown"
    treatment_state: TreatmentState = "unknown"
    pinpoint: str | None = Field(default=None, max_length=300)
    quote: str | None = Field(default=None, max_length=100_000)
    exclusion_reason: str | None = Field(default=None, max_length=100_000)
    assigned_reviewer_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    sort_order: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("title")
    @classmethod
    def record_title_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @model_validator(mode="after")
    def cited_records_require_a_source(self):
        if self.evidence_class == "cited" and not self.source_url:
            raise ValueError("cited evidence requires an exact source_url")
        if self.record_type == "exclusion" and not self.exclusion_reason:
            raise ValueError("exclusion records require exclusion_reason")
        return self


class RecordUpdate(RecordCreate):
    revision: int = Field(ge=1)


class SnapshotCreate(BaseModel):
    label: str | None = Field(default=None, max_length=240)
