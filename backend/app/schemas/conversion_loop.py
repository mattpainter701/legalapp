import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IntakeFormCreate(BaseModel):
    slug: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=200)
    form_schema: dict[str, Any] = Field(default_factory=dict, alias="schema_json")
    is_active: bool = True

    model_config = {"populate_by_name": True}


class IntakeFormResponse(IntakeFormCreate):
    id: uuid.UUID
    tenant_id: uuid.UUID
    version: int
    created_at: datetime

    model_config = {"from_attributes": True}


class IntakeSubmissionCreate(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    attribution: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=200)
    website: str | None = Field(default=None, max_length=200)
    email_consent: bool = False
    sms_consent: bool = False
    disclosure_version: str | None = Field(default=None, max_length=80)


class BookingCreate(BaseModel):
    lead_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    timezone: str = Field(default="UTC", max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=200)


class ConsentUpdate(BaseModel):
    email_allowed: bool = False
    sms_allowed: bool = False
    phone_verified: bool = False
    disclosure_version: str = Field(min_length=1, max_length=80)


class TriageDecision(BaseModel):
    decision: str = Field(pattern=r"^(clear|hold|decline)$")
    note: str | None = Field(default=None, max_length=2000)


class FollowUpCreate(BaseModel):
    channel: str = Field(pattern=r"^(email|sms)$")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    idempotency_key: str = Field(min_length=8, max_length=200)
