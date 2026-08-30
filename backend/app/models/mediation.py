"""Mediation Platform sub-entity models.

The ``MediationCase`` and ``MediationCaseEvent`` models live in
``app.models.plugin`` (the original skeleton, since extended). These are the
mediation-platform sub-entities that hang off a case: parties, portal invites,
the marital asset/debt schedule, the document vault, and settlement proposals.
All are tenant-isolated via RLS (see migration 031) and cascade-delete with
their parent case. Mirrors the Trust & Estate sub-entity patterns in
``app.models.estate``.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MediationParty(Base):
    """A participant on a mediation case.

    ``role`` is one of: our_client | opposing_party | mediator | counsel.
    A firm client party may be linked to a ``User`` (role="client") for portal
    login; an opposing party gets magic-link token access instead.
    """

    __tablename__ = "mediation_parties"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(50), default="our_client", server_default="our_client"
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_initiator: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    case: Mapped["object"] = relationship(
        "MediationCase", back_populates="case_parties"
    )


class MediationInvite(Base):
    """Tokenized portal access for a party.

    ``token_hash`` stores the sha256 of the random invite token; the raw token
    is only ever emailed, never persisted. ``kind`` is client_account (firm
    client, becomes a User login) or portal_magic (opposing party, scoped JWT).
    """

    __tablename__ = "mediation_invites"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_parties.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(
        String(30), default="portal_magic", server_default="portal_magic"
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = _created_at()

    case: Mapped["object"] = relationship("MediationCase", back_populates="invites")


class MediationAsset(Base):
    """A line item on the marital asset & debt schedule (financial disclosure).

    Status lifecycle:
        draft -> submitted -> attorney_approved -> sent
              -> (opposing_approved | disputed)
    """

    __tablename__ = "mediation_assets"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(20), default="asset", server_default="asset"
    )  # asset | debt
    category: Mapped[str | None] = mapped_column(String(150), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    owned_by: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # party_a | party_b | joint
    claimed_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    status: Mapped[str] = mapped_column(
        String(40), default="draft", server_default="draft"
    )
    submitted_by_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_parties.id", ondelete="SET NULL"),
        nullable=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attorney_approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    attorney_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opposing_decision: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )  # approved | disputed
    opposing_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dispute_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    case: Mapped["object"] = relationship("MediationCase", back_populates="assets")


class MediationDocument(Base):
    """Secure document vault entry — a file attached to a case (or asset)."""

    __tablename__ = "mediation_documents"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_by_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_parties.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    case: Mapped["object"] = relationship("MediationCase", back_populates="documents")
    recipients: Mapped[list["MediationDocumentRecipient"]] = relationship(
        "MediationDocumentRecipient",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class MediationDocumentRecipient(Base):
    """A deliberate release of one immutable document to one case party."""

    __tablename__ = "mediation_document_recipients"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "party_id",
            name="uq_mediation_document_recipient",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_parties.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    released_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    released_at: Mapped[datetime] = _created_at()
    first_viewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_downloaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    document: Mapped["MediationDocument"] = relationship(
        "MediationDocument", back_populates="recipients"
    )


class MediationProposal(Base):
    """A settlement proposal or counter-proposal in the negotiation thread."""

    __tablename__ = "mediation_proposals"

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    proposed_by_party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_parties.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_proposals.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="open", server_default="open"
    )  # open | accepted | rejected | superseded
    review_state: Mapped[str] = mapped_column(
        String(30), default="pending", server_default="pending"
    )  # pending | approved | changes_requested | rejected
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    case: Mapped["object"] = relationship("MediationCase", back_populates="proposals")
    recipients: Mapped[list["MediationProposalRecipient"]] = relationship(
        "MediationProposalRecipient",
        back_populates="proposal",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class MediationProposalRecipient(Base):
    """A recipient-specific release of an attorney-approved proposal."""

    __tablename__ = "mediation_proposal_recipients"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id",
            "party_id",
            name="uq_mediation_proposal_recipient",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mediation_parties.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    released_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    released_at: Mapped[datetime] = _created_at()
    first_viewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    proposal: Mapped["MediationProposal"] = relationship(
        "MediationProposal", back_populates="recipients"
    )
