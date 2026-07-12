"""Pydantic schemas for the Mediation Platform module."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel


# ── Sessions (activity log) ────────────────────────────────────────────────────


class SessionCreate(BaseModel):
    session_type: Optional[str] = "other"
    title: str
    content: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    session_type: Optional[str] = None
    event_type: Optional[str] = None
    title: str
    content: Optional[str] = None
    added_by: Optional[str] = None
    created_at: datetime


# ── Case ────────────────────────────────────────────────────────────────────


class MediationCaseCreate(BaseModel):
    case_name: str
    party_a: Optional[str] = None
    party_b: Optional[str] = None
    dispute_type: Optional[str] = None
    mediation_stage: Optional[str] = "Pre-Session"
    mediator: Optional[str] = None
    attorney: Optional[str] = None
    claim_value: Optional[str] = None
    scheduled_session: Optional[datetime] = None
    confidentiality_signed: Optional[bool] = False
    summary: Optional[str] = None
    matter_id: Optional[str] = None
    client_contact_id: Optional[str] = None


class MediationCaseUpdate(BaseModel):
    case_name: Optional[str] = None
    party_a: Optional[str] = None
    party_b: Optional[str] = None
    dispute_type: Optional[str] = None
    mediation_stage: Optional[str] = None
    mediator: Optional[str] = None
    attorney: Optional[str] = None
    claim_value: Optional[str] = None
    scheduled_session: Optional[datetime] = None
    confidentiality_signed: Optional[bool] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    matter_id: Optional[str] = None
    client_contact_id: Optional[str] = None


class MediationCaseResponse(BaseModel):
    id: str
    case_name: Optional[str] = None
    title: str
    party_a: Optional[str] = None
    party_b: Optional[str] = None
    dispute_type: Optional[str] = None
    mediation_stage: Optional[str] = None
    mediator: Optional[str] = None
    attorney: Optional[str] = None
    claim_value: Optional[str] = None
    scheduled_session: Optional[datetime] = None
    confidentiality_signed: bool = False
    status: str
    summary: Optional[str] = None
    matter_id: Optional[str] = None
    client_contact_id: Optional[str] = None
    parties_count: int = 0
    assets_count: int = 0
    created_at: datetime
    updated_at: datetime


class MediationCaseDetail(BaseModel):
    """Wrapper the detail page expects: {mediation, sessions}."""

    mediation: MediationCaseResponse
    sessions: List[SessionResponse]


class MediationStats(BaseModel):
    total: int
    active: int
    scheduled: int
    settled: int
    closed: int
    pending_confidentiality: int


# ── Parties ───────────────────────────────────────────────────────────────────


class PartyCreate(BaseModel):
    name: str
    role: str = "our_client"
    email: Optional[str] = None
    contact_id: Optional[str] = None
    is_initiator: bool = False


class PartyUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    contact_id: Optional[str] = None
    is_initiator: Optional[bool] = None


class PartyResponse(BaseModel):
    id: str
    case_id: str
    name: str
    role: str
    email: Optional[str] = None
    contact_id: Optional[str] = None
    user_id: Optional[str] = None
    is_initiator: bool
    has_account: bool = False
    invited: bool = False
    created_at: datetime


class InviteResponse(BaseModel):
    id: str
    party_id: str
    kind: str
    email: Optional[str] = None
    invite_url: str
    email_sent: Optional[bool] = None
    delivery_error: Optional[str] = None
    expires_at: datetime


# ── Assets (marital asset & debt schedule) ──────────────────────────────────────


class AssetCreate(BaseModel):
    description: str
    kind: str = "asset"  # asset | debt
    category: Optional[str] = None
    value: Optional[Decimal] = None
    owned_by: Optional[str] = None  # party_a | party_b | joint
    claimed_by: Optional[str] = None
    notes: Optional[str] = None


class AssetUpdate(BaseModel):
    description: Optional[str] = None
    kind: Optional[str] = None
    category: Optional[str] = None
    value: Optional[Decimal] = None
    owned_by: Optional[str] = None
    claimed_by: Optional[str] = None
    notes: Optional[str] = None


class AssetDecision(BaseModel):
    decision: str  # approved | disputed
    dispute_reason: Optional[str] = None


class AssetResponse(BaseModel):
    id: str
    case_id: str
    kind: str
    category: Optional[str] = None
    description: str
    value: Optional[Decimal] = None
    owned_by: Optional[str] = None
    claimed_by: Optional[str] = None
    status: str
    submitted_by_party_id: Optional[str] = None
    submitted_at: Optional[datetime] = None
    attorney_approved_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    opposing_decision: Optional[str] = None
    opposing_decided_at: Optional[datetime] = None
    dispute_reason: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ── Documents (vault) ───────────────────────────────────────────────────────────


class DocumentResponse(BaseModel):
    id: str
    case_id: str
    asset_id: Optional[str] = None
    filename: str
    content_type: Optional[str] = None
    file_size: Optional[int] = None
    description: Optional[str] = None
    uploaded_by_party_id: Optional[str] = None
    uploaded_by_user_id: Optional[str] = None
    created_at: datetime


# ── Proposals ───────────────────────────────────────────────────────────────────


class ProposalCreate(BaseModel):
    title: str
    body: Optional[str] = None
    parent_proposal_id: Optional[str] = None


class ProposalStatusUpdate(BaseModel):
    status: str  # accepted | rejected


class ProposalResponse(BaseModel):
    id: str
    case_id: str
    proposed_by_party_id: Optional[str] = None
    proposed_by_name: Optional[str] = None
    parent_proposal_id: Optional[str] = None
    title: str
    body: Optional[str] = None
    status: str
    created_at: datetime


# ── Portal ──────────────────────────────────────────────────────────────────────


class PortalAcceptRequest(BaseModel):
    token: str


class PortalAcceptResponse(BaseModel):
    case_id: str
    party_id: str
    party_role: str
    kind: str


class PortalCaseView(BaseModel):
    case: MediationCaseResponse
    party_role: str
    party_id: str
    my_assets: List[AssetResponse]
    shared_assets: List[AssetResponse]
    documents: List[DocumentResponse]
    proposals: List[ProposalResponse]
