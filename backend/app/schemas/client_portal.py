"""Pydantic schemas for the client portal (matter-scoped client access)."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

# ── Invite acceptance (client side) ─────────────────────────────────────────


class ClientPortalAcceptRequest(BaseModel):
    token: str


class ClientPortalAcceptResponse(BaseModel):
    matter_id: str
    matter_name: str


# ── Matter overview (client side) ───────────────────────────────────────────


class PortalAttorney(BaseModel):
    name: str
    role: str | None = None


class PortalMatterView(BaseModel):
    matter_id: str
    matter_name: str
    status: str | None = None
    stage: str | None = None
    practice_area: str | None = None
    description: str | None = None
    key_dates: dict | None = None
    attorneys: list[PortalAttorney] = []


# ── Messages ────────────────────────────────────────────────────────────────


class PortalMessageCreate(BaseModel):
    subject: str | None = None
    body: str


class PortalMessageResponse(BaseModel):
    id: str
    direction: str
    subject: str
    body: str | None = None
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Documents ───────────────────────────────────────────────────────────────


class PortalDocumentResponse(BaseModel):
    id: str
    filename: str
    content_type: str | None = None
    file_size: int | None = None
    description: str | None = None
    uploaded_by_client: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Invoices ────────────────────────────────────────────────────────────────


class PortalInvoiceResponse(BaseModel):
    id: str
    invoice_number: str
    status: str
    issue_date: date
    due_date: date
    total: Decimal
    stripe_payment_link: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Firm-side invite management ─────────────────────────────────────────────


class FirmInviteCreate(BaseModel):
    contact_id: str | None = None
    email: str | None = None


class FirmInviteResponse(BaseModel):
    id: str
    matter_id: str
    email: str | None = None
    invite_url: str | None = None
    email_sent: bool | None = None
    delivery_error: str | None = None
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked: bool

    model_config = ConfigDict(from_attributes=True)
