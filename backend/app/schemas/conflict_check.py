"""API contracts for saved conflict searches."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ConflictCheckCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    names: list[str] = Field(default_factory=list, max_length=25)
    emails: list[str] = Field(default_factory=list, max_length=25)
    organization_names: list[str] = Field(default_factory=list, max_length=25)
    matter_id: str | None = None

    @model_validator(mode="after")
    def require_a_term(self):
        if not any(
            value.strip()
            for values in (self.names, self.emails, self.organization_names)
            for value in values
        ):
            raise ValueError("Enter at least one name, organization, or email address")
        return self


class ConflictCheckClose(BaseModel):
    decision: Literal[
        "no_conflict_found", "conflict_found", "cleared_with_conditions"
    ]
    notes: str = Field(min_length=1, max_length=10000)
    acknowledge_attorney_review: bool


class ConflictCheckResponse(BaseModel):
    id: str
    matter_id: str | None
    label: str
    query: dict
    matches: list[dict]
    match_count: int
    restricted_matter_count: int
    status: str
    decision: str
    notes: str | None
    created_by_user_id: str | None
    closed_by_user_id: str | None
    created_at: datetime
    closed_at: datetime | None


class ConflictCheckList(BaseModel):
    items: list[ConflictCheckResponse]
    total: int
