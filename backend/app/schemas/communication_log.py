"""Pydantic schemas for communication logs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class CommunicationLogCreate(BaseModel):
    direction: str = "outbound"
    channel: str = "email"
    status: str = "logged"
    subject: str
    body: Optional[str] = None
    summary: Optional[str] = None
    matter_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None
    occurred_at: Optional[datetime] = None
    external_ref: Optional[str] = None

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        allowed = {"inbound", "outbound"}
        if v not in allowed:
            raise ValueError(f"direction must be one of {allowed}")
        return v

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str) -> str:
        allowed = {"email", "call", "letter", "meeting", "portal", "sms", "other"}
        if v not in allowed:
            raise ValueError(f"channel must be one of {allowed}")
        return v


class CommunicationLogUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[str] = None
    matter_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None


class CommunicationLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    direction: str
    channel: str
    status: str
    subject: str
    body: Optional[str]
    summary: Optional[str]
    matter_id: Optional[uuid.UUID]
    contact_id: Optional[uuid.UUID]
    created_by_user_id: Optional[uuid.UUID]
    occurred_at: datetime
    external_ref: Optional[str]
    created_at: datetime
    updated_at: datetime


class CommunicationLogListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[CommunicationLogResponse]
    total: int
