"""Pydantic schemas for contacts and conflict checks."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


class AddressSchema(BaseModel):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None


class ContactCreate(BaseModel):
    entity_type: str = "person"
    contact_type: str = "client"
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    organization_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    secondary_phone: Optional[str] = None
    address: Optional[AddressSchema] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None

    @field_validator("entity_type")
    @classmethod
    def validate_entity_type(cls, v: str) -> str:
        allowed = {"person", "organization"}
        if v not in allowed:
            raise ValueError(f"entity_type must be one of {allowed}")
        return v

    @field_validator("contact_type")
    @classmethod
    def validate_contact_type(cls, v: str) -> str:
        allowed = {
            "client", "opposing_party", "witness", "expert",
            "vendor", "referral", "other",
        }
        if v not in allowed:
            raise ValueError(f"contact_type must be one of {allowed}")
        return v


class ContactUpdate(BaseModel):
    entity_type: Optional[str] = None
    contact_type: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    organization_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    secondary_phone: Optional[str] = None
    address: Optional[AddressSchema] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    entity_type: str
    contact_type: str
    first_name: Optional[str]
    last_name: Optional[str]
    organization_name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    secondary_phone: Optional[str]
    address: Optional[Any]
    notes: Optional[str]
    tags: Optional[list]
    is_active: bool
    display_name: str
    created_by_user_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class ContactListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[ContactResponse]
    total: int


class ConflictCheckRequest(BaseModel):
    names: list[str] = []
    emails: list[str] = []
    organization_names: list[str] = []


class ConflictMatch(BaseModel):
    contact_id: uuid.UUID
    display_name: str
    contact_type: str
    email: Optional[str]
    match_field: str
    match_value: str
    matter_ids: list[uuid.UUID]
    matter_names: list[str]


class ConflictCheckResult(BaseModel):
    clear: bool
    matches: list[ConflictMatch]
    checked_names: list[str]
    checked_emails: list[str]


# --- Lead / Intake schemas ---

class LeadCreate(BaseModel):
    contact_id: Optional[uuid.UUID] = None
    # Or create contact inline:
    contact: Optional[ContactCreate] = None
    source: Optional[str] = None
    practice_area: Optional[str] = None
    description: Optional[str] = None
    estimated_value: Optional[Decimal] = None
    assigned_to_user_id: Optional[uuid.UUID] = None


class LeadUpdate(BaseModel):
    status: Optional[str] = None
    source: Optional[str] = None
    practice_area: Optional[str] = None
    description: Optional[str] = None
    estimated_value: Optional[Decimal] = None
    assigned_to_user_id: Optional[uuid.UUID] = None
    conflict_check_status: Optional[str] = None
    conflict_check_notes: Optional[str] = None
    declined_reason: Optional[str] = None


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    contact_id: uuid.UUID
    contact: Optional[ContactResponse] = None
    status: str
    source: Optional[str]
    practice_area: Optional[str]
    description: Optional[str]
    estimated_value: Optional[Decimal]
    assigned_to_user_id: Optional[uuid.UUID]
    conflict_check_status: str
    conflict_check_notes: Optional[str]
    matter_id: Optional[uuid.UUID]
    declined_reason: Optional[str]
    created_by_user_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class LeadConvertRequest(BaseModel):
    matter_name: str
    matter_type: str
    role: str = "Plaintiff"
    jurisdiction: str
    counterparty: str = ""
