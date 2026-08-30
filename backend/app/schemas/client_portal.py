"""Pydantic schemas for the client portal (matter-scoped client access)."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Client-supplied free text is bounded at the edge. Nothing downstream truncates
# it, and the portal is reachable by anyone holding a link, so the body column
# is not a place to accept unbounded input.
MAX_MESSAGE_BODY = 10_000
MAX_MESSAGE_SUBJECT = 200
MAX_DOCUMENT_DESCRIPTION = 500


# ── Invite acceptance (client side) ─────────────────────────────────────────


class ClientPortalAcceptRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ClientPortalActivateRequest(ClientPortalAcceptRequest):
    password: str = Field(min_length=12, max_length=128)


class ClientPortalLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    matter_id: str


class ClientPortalAcceptResponse(BaseModel):
    matter_id: str
    matter_name: str


class ClientPortalLoginResponse(ClientPortalAcceptResponse):
    email: str


# ── Session ─────────────────────────────────────────────────────────────────


class PortalSessionResponse(BaseModel):
    """Who the portal thinks you are, and how long the session has left."""

    matter_id: str
    matter_name: str
    email: str | None = None
    expires_at: datetime
    invite_expires_at: datetime


# ── Matter overview (client side) ───────────────────────────────────────────


class PortalAttorney(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None


class PortalKeyDate(BaseModel):
    label: str
    value: str
    # Named ``iso_date`` rather than ``date`` so the annotation below is not
    # shadowed by the field itself inside the class body.
    iso_date: date | None = None
    is_past: bool = False
    days_away: int | None = None


class PortalMatterView(BaseModel):
    matter_id: str
    matter_name: str
    status: str | None = None
    stage: str | None = None
    practice_area: str | None = None
    description: str | None = None
    # Retained as the raw firm-entered mapping for backwards compatibility;
    # ``key_date_list`` is the parsed, sorted view the portal renders.
    key_dates: dict | None = None
    key_date_list: list[PortalKeyDate] = []
    next_key_date: PortalKeyDate | None = None
    attorneys: list[PortalAttorney] = []
    # At-a-glance counters so the client's landing tab answers "is anything
    # waiting on me?" without visiting every tab.
    unread_message_count: int = 0
    document_count: int = 0
    pending_signature_count: int = 0
    open_invoice_count: int = 0
    outstanding_balance: Decimal = Decimal("0")
    last_activity_at: datetime | None = None


class PortalMediationCase(BaseModel):
    """Client-safe subset of a mediation case linked to the current matter."""

    id: str
    case_name: str | None = None
    party_a: str | None = None
    party_b: str | None = None
    dispute_type: str | None = None
    stage: str | None = None
    status: str
    mediator: str | None = None
    scheduled_session: datetime | None = None
    confidentiality_signed: bool = False


class PortalMediationAsset(BaseModel):
    id: str
    kind: str
    category: str | None = None
    description: str
    value: Decimal | None = None
    owned_by: str | None = None
    status: str
    opposing_decision: str | None = None
    dispute_reason: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class PortalMediationDocument(BaseModel):
    id: str
    filename: str
    content_type: str | None = None
    file_size: int | None = None
    description: str | None = None
    is_own: bool
    release_state: str
    released_at: datetime | None = None
    created_at: datetime
    download_url: str


class PortalMediationProposal(BaseModel):
    id: str
    parent_proposal_id: str | None = None
    title: str
    body: str | None = None
    proposed_by_name: str | None = None
    is_own: bool
    status: str
    review_state: str
    review_notes: str | None = None
    release_state: str
    released_at: datetime | None = None
    created_at: datetime


class PortalMediationView(BaseModel):
    """Mediation data scoped to the native matter-portal identity."""

    case: PortalMediationCase
    party_id: str
    party_role: str
    own_assets: list[PortalMediationAsset] = Field(default_factory=list)
    shared_assets: list[PortalMediationAsset] = Field(default_factory=list)
    documents: list[PortalMediationDocument] = Field(default_factory=list)
    proposals: list[PortalMediationProposal] = Field(default_factory=list)


# ── Messages ────────────────────────────────────────────────────────────────


class PortalMessageCreate(BaseModel):
    subject: str | None = Field(default=None, max_length=MAX_MESSAGE_SUBJECT)
    body: str = Field(min_length=1, max_length=MAX_MESSAGE_BODY)

    @field_validator("subject", "body")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("Message body cannot be empty")
        return value


class PortalMessageResponse(BaseModel):
    id: str
    direction: str
    subject: str
    body: str | None = None
    occurred_at: datetime
    # True for firm messages the client had not yet read when the list was built.
    unread: bool = False

    model_config = ConfigDict(from_attributes=True)


class PortalMessageList(BaseModel):
    messages: list[PortalMessageResponse] = []
    unread_count: int = 0
    total: int = 0
    has_more: bool = False


class PortalMarkReadResponse(BaseModel):
    messages_seen_at: datetime
    unread_count: int = 0


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
    amount_paid: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")
    # "overdue" is derived from due_date against the balance, never stored.
    is_overdue: bool = False
    days_overdue: int = 0
    payment_terms: str | None = None
    stripe_payment_link: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PortalInvoiceList(BaseModel):
    invoices: list[PortalInvoiceResponse] = []
    total_billed: Decimal = Decimal("0")
    total_paid: Decimal = Decimal("0")
    outstanding_balance: Decimal = Decimal("0")
    overdue_balance: Decimal = Decimal("0")


class PortalInvoicePaymentResponse(BaseModel):
    invoice_id: str
    amount: Decimal
    currency: str = "usd"
    payment_intent_id: str
    client_secret: str | None = None
    checkout_url: str | None = None
    checkout_session_id: str | None = None


# ── Firm-side invite management ─────────────────────────────────────────────


class FirmInviteCreate(BaseModel):
    contact_id: str | None = None
    email: str | None = Field(default=None, max_length=320)
    # Revoke every other live invite for this matter as the new one is issued.
    # Off by default so multiple client-side recipients stay supported.
    revoke_existing: bool = False


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
    last_seen_at: datetime | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
