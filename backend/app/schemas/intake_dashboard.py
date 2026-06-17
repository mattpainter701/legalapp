"""Schemas for the local receptionist intake dashboard."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IntakeSearchResult(BaseModel):
    id: str
    result_type: Literal["contact", "lead", "matter", "legacy_call"]
    title: str
    subtitle: Optional[str] = None
    phone: Optional[str] = None
    normalized_phone: Optional[str] = None
    practice_area: Optional[str] = None
    prior_attorney_name: Optional[str] = None
    occurred_at: Optional[datetime] = None
    contact_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    matter_id: Optional[uuid.UUID] = None
    legacy_call_record_id: Optional[uuid.UUID] = None
    score: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntakeDashboardSearchResponse(BaseModel):
    query: Optional[str] = None
    phone: Optional[str] = None
    normalized_phone: Optional[str] = None
    history_found: bool
    identity_warning: Optional[str] = None
    recommended_attorney_name: Optional[str] = None
    results: list[IntakeSearchResult]


class IntakeDashboardCallCreate(BaseModel):
    caller_name: Optional[str] = Field(None, max_length=500)
    phone: Optional[str] = Field(None, max_length=100)
    practice_area: Optional[str] = Field(None, max_length=100)
    purpose: Optional[str] = None
    outcome: Literal["log_only", "create_lead"] = "log_only"
    qualified: bool = False
    source: Optional[str] = Field("phone", max_length=50)
    existing_contact_id: Optional[uuid.UUID] = None
    existing_lead_id: Optional[uuid.UUID] = None
    assigned_to_user_id: Optional[uuid.UUID] = None
    occurred_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("caller_name", "phone", "purpose", "notes", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class IntakeDashboardCallResponse(BaseModel):
    communication_id: uuid.UUID
    contact_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    created_lead: bool = False
    status: str


class RotationRuleInput(BaseModel):
    practice_area: str = Field(..., min_length=1, max_length=100)
    eligible_user_ids: list[uuid.UUID] = Field(default_factory=list)
    is_enabled: bool = True


class RotationRuleUpsertRequest(BaseModel):
    rules: list[RotationRuleInput]


class RotationRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    practice_area: str
    eligible_user_ids: list
    last_assigned_user_id: Optional[uuid.UUID]
    is_enabled: bool
    created_by_user_id: Optional[uuid.UUID]
    updated_by_user_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class RotationRuleListResponse(BaseModel):
    rules: list[RotationRuleResponse]


class AssignNextResponse(BaseModel):
    lead_id: uuid.UUID
    assigned_to_user_id: uuid.UUID
    assigned_to_name: Optional[str] = None
    practice_area: str
    rotation_rule_id: uuid.UUID
