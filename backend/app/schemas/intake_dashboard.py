"""Schemas for the local receptionist intake dashboard."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IntakeSearchResult(BaseModel):
    id: str
    result_type: Literal["contact", "lead", "matter", "legacy_call", "call_log"]
    title: str
    subtitle: Optional[str] = None
    phone: Optional[str] = None
    normalized_phone: Optional[str] = None
    practice_area: Optional[str] = None
    prior_attorney_name: Optional[str] = None
    prior_attorney_user_id: Optional[uuid.UUID] = None
    occurred_at: Optional[datetime] = None
    answered_by: Optional[str] = None
    result: Optional[str] = None
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
    recommended_attorney_user_id: Optional[uuid.UUID] = None
    results: list[IntakeSearchResult]


class RecentIntakeCaller(BaseModel):
    id: uuid.UUID
    caller_name: str
    phone: Optional[str] = None
    normalized_phone: Optional[str] = None
    direction: Optional[str] = None
    caller_number: Optional[str] = None
    callee_number: Optional[str] = None
    is_internal_call: bool = False
    internal_call_type: Optional[str] = None
    practice_area: Optional[str] = None
    purpose: Optional[str] = None
    notes: Optional[str] = None
    contact_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    lead_status: Optional[str] = None
    assigned_to_user_id: Optional[uuid.UUID] = None
    assigned_to_name: Optional[str] = None
    task_id: Optional[uuid.UUID] = None
    task_status: Optional[str] = None
    task_priority: Optional[str] = None
    task_due_date: Optional[date] = None
    task_completed_at: Optional[datetime] = None
    task_viewed_at: Optional[datetime] = None
    task_customer_contacted_at: Optional[datetime] = None
    task_customer_contact_method: Optional[str] = None
    created_by_user_id: Optional[uuid.UUID] = None
    created_by_name: Optional[str] = None
    occurred_at: datetime
    source: str = "manual"
    answered_by: Optional[str] = None
    result: Optional[str] = None
    duration_seconds: Optional[int] = None
    has_call_summary: bool = False
    has_transcript: bool = False
    can_view_confidential_call_content: bool = False
    call_summary: Optional[str] = None
    transcript_text: Optional[str] = None
    recording_url: Optional[str] = None
    transcript_url: Optional[str] = None


class RecentIntakeCallersResponse(BaseModel):
    limit: int
    callers: list[RecentIntakeCaller]


class AssignmentAvailabilityResponse(BaseModel):
    practice_area: str
    can_assign: bool
    reason: Optional[str] = None
    rule_practice_area: Optional[str] = None
    eligible_count: int = 0


class IntakeDashboardCallCreate(BaseModel):
    caller_name: Optional[str] = Field(None, max_length=500)
    phone: Optional[str] = Field(None, max_length=100)
    practice_area: Optional[str] = Field(None, max_length=100)
    purpose: Optional[str] = None
    outcome: Literal["log_only", "create_lead"] = "log_only"
    task_mode: Literal["partner_rotation", "specific_staff", "none"] = (
        "partner_rotation"
    )
    task_assigned_to_user_id: Optional[uuid.UUID] = None
    task_title: Optional[str] = Field(None, max_length=500)
    task_description: Optional[str] = None
    qualified: bool = False
    source: Optional[str] = Field("phone", max_length=50)
    existing_contact_id: Optional[uuid.UUID] = None
    existing_lead_id: Optional[uuid.UUID] = None
    existing_communication_id: Optional[uuid.UUID] = None
    assigned_to_user_id: Optional[uuid.UUID] = None
    occurred_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator(
        "caller_name",
        "phone",
        "purpose",
        "notes",
        "task_title",
        "task_description",
        mode="before",
    )
    @classmethod
    def blank_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class IntakeDashboardCallResponse(BaseModel):
    communication_id: uuid.UUID
    contact_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    task_id: Optional[uuid.UUID] = None
    created_lead: bool = False
    status: str


class ZoomPhoneCallItem(BaseModel):
    id: uuid.UUID
    caller_name: str
    phone: Optional[str] = None
    normalized_phone: Optional[str] = None
    direction: str
    caller_number: Optional[str] = None
    callee_number: Optional[str] = None
    is_internal_call: bool = False
    internal_call_type: Optional[str] = None
    result: Optional[str] = None
    duration_seconds: Optional[Any] = None
    summary: Optional[str] = None
    transcript_url: Optional[str] = None
    recording_url: Optional[str] = None
    occurred_at: datetime
    contact_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    external_ref: Optional[str] = None


class ZoomPhoneCallsResponse(BaseModel):
    calls: list[ZoomPhoneCallItem]


class ZoomPhoneSyncResponse(BaseModel):
    imported: int
    updated: int
    skipped: int


class IntakeCallDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    created_by_user_id: uuid.UUID | None = None
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class IntakeCallDraftUpsertRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


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
    task_id: Optional[uuid.UUID] = None


class PartnerAssignmentLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    assignment_method: str
    assigned_to_user_id: Optional[uuid.UUID] = None
    assigned_to_name: Optional[str] = None
    assigned_by_name: Optional[str] = None
    practice_area: Optional[str] = None
    lead_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None
    communication_id: Optional[uuid.UUID] = None


class PartnerAssignmentLogResponse(BaseModel):
    entries: list[PartnerAssignmentLogEntry]
