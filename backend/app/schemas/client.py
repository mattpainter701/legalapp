"""Validated API contracts for the dedicated client CRM workspace."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

ClientStatus = Literal["prospect", "active", "inactive", "former"]
ContactMethod = Literal["email", "phone", "sms", "mail", "portal"]
PaymentMethod = Literal["stripe", "check", "ach", "wire", "cash", "other"]
BillingDeliveryMethod = Literal["email", "mail", "portal"]


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")

    street: str | None = Field(default=None, max_length=300)
    street2: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=200)
    state: str | None = Field(default=None, max_length=100)
    zip: str | None = Field(default=None, max_length=30)
    country: str | None = Field(default=None, max_length=100)


class EmergencyContact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=300)
    relationship: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = None


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["person", "organization"] = "person"
    client_status: ClientStatus = "active"
    client_number: str | None = Field(default=None, max_length=100)
    first_name: str | None = Field(default=None, max_length=200)
    last_name: str | None = Field(default=None, max_length=200)
    preferred_name: str | None = Field(default=None, max_length=200)
    organization_name: str | None = Field(default=None, max_length=500)
    date_of_birth: date | None = None
    client_since: date | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    secondary_phone: str | None = Field(default=None, max_length=50)
    address: Address | None = None
    preferred_contact_method: ContactMethod | None = None
    preferred_contact_window: str | None = Field(default=None, max_length=200)
    preferred_contact_timezone: str | None = Field(default=None, max_length=100)
    preferred_language: str | None = Field(default=None, max_length=100)
    emergency_contact: EmergencyContact | None = None
    sms_opt_in: bool = False
    email_opt_in: bool = True
    referral_source: str | None = Field(default=None, max_length=300)
    preferred_payment_method: PaymentMethod | None = None
    billing_delivery_method: BillingDeliveryMethod = "email"
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    billing_notes: str | None = Field(default=None, max_length=10_000)
    qbo_customer_id: str | None = Field(default=None, max_length=100)
    stripe_customer_id: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=50_000)
    tags: list[str] | None = Field(default=None, max_length=50)

    @field_validator(
        "client_number",
        "first_name",
        "last_name",
        "preferred_name",
        "organization_name",
        "phone",
        "secondary_phone",
        "preferred_language",
        "preferred_contact_window",
        "preferred_contact_timezone",
        "referral_source",
        "qbo_customer_id",
        "stripe_customer_id",
        mode="before",
    )
    @classmethod
    def blank_to_none(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def validate_name(self):
        if self.entity_type == "organization":
            if not self.organization_name:
                raise ValueError("organization_name is required for organizations")
        elif not (self.first_name or self.last_name):
            raise ValueError("first_name or last_name is required for people")
        return self


class ClientUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["person", "organization"] | None = None
    client_status: ClientStatus | None = None
    client_number: str | None = Field(default=None, max_length=100)
    first_name: str | None = Field(default=None, max_length=200)
    last_name: str | None = Field(default=None, max_length=200)
    preferred_name: str | None = Field(default=None, max_length=200)
    organization_name: str | None = Field(default=None, max_length=500)
    date_of_birth: date | None = None
    client_since: date | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    secondary_phone: str | None = Field(default=None, max_length=50)
    address: Address | None = None
    preferred_contact_method: ContactMethod | None = None
    preferred_contact_window: str | None = Field(default=None, max_length=200)
    preferred_contact_timezone: str | None = Field(default=None, max_length=100)
    preferred_language: str | None = Field(default=None, max_length=100)
    emergency_contact: EmergencyContact | None = None
    sms_opt_in: bool | None = None
    email_opt_in: bool | None = None
    referral_source: str | None = Field(default=None, max_length=300)
    preferred_payment_method: PaymentMethod | None = None
    billing_delivery_method: BillingDeliveryMethod | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    billing_notes: str | None = Field(default=None, max_length=10_000)
    qbo_customer_id: str | None = Field(default=None, max_length=100)
    stripe_customer_id: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=50_000)
    tags: list[str] | None = Field(default=None, max_length=50)
    is_active: bool | None = None


class ClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    entity_type: str
    contact_type: str
    client_status: str | None
    client_number: str | None
    first_name: str | None
    last_name: str | None
    preferred_name: str | None
    organization_name: str | None
    display_name: str
    date_of_birth: date | None
    client_since: date | None
    email: str | None
    phone: str | None
    secondary_phone: str | None
    address: dict | None
    preferred_contact_method: str | None
    preferred_contact_window: str | None
    preferred_contact_timezone: str | None
    preferred_language: str | None
    emergency_contact: dict | None
    sms_opt_in: bool
    sms_opt_in_at: datetime | None
    email_opt_in: bool
    referral_source: str | None
    last_contacted_at: datetime | None
    preferred_payment_method: str | None
    billing_delivery_method: str
    payment_terms_days: int
    billing_notes: str | None
    qbo_customer_id: str | None
    qbo_synced_at: datetime | None
    stripe_customer_id: str | None
    notes: str | None
    tags: list | None
    is_active: bool
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ClientRelatedContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    first_name: str | None
    last_name: str | None
    preferred_name: str | None
    organization_name: str | None
    display_name: str
    email: str | None
    phone: str | None
    secondary_phone: str | None
    preferred_contact_method: str | None
    client_contact_role: str | None
    is_primary_client_contact: bool
    client_contact_authorization: str | None


class ClientListResponse(BaseModel):
    items: list[ClientResponse]
    total: int


class ClientSummaryResponse(BaseModel):
    total: int
    active: int
    prospects: int
    inactive: int
    former: int
    sms_opted_in: int


class ClientImportError(BaseModel):
    row: int
    detail: str


class ClientImportResponse(BaseModel):
    created: int
    updated: int
    skipped: int
    errors: list[ClientImportError]


class ClientQBOSyncResponse(BaseModel):
    # "demo_simulated" is recorded locally by a demo workspace and never
    # involves a QuickBooks request; callers must not treat it as a live sync.
    status: Literal["synced", "demo_simulated"]
    client_id: uuid.UUID
    qbo_customer_id: str
    synced_at: datetime
    detail: str | None = None
