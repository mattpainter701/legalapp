"""SQLAlchemy models for native e-signature (Epic 2).

A ``SignatureRequest`` is one "send" of a matter document for signature; each
``SignatureSigner`` is a party who must sign. Signing happens inside the client
portal: the signer fills the document's own form fields and adopts a typed
legal name as the signature, or uploads a signed copy for the firm to accept.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class SignatureRequest(Base):
    __tablename__ = "signature_requests"
    __table_args__ = (
        Index("ix_signature_requests_matter_id", "matter_id"),
        UniqueConstraint("tenant_id", "id", name="uq_signature_requests_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    # draft | sent | partially_signed | completed | declined | expired | voided
    status: Mapped[str] = mapped_column(
        String(50), default="draft", server_default="draft"
    )
    # Always "internal": signing happens in the client portal.
    provider: Mapped[str] = mapped_column(
        String(50), default="internal", server_default="internal"
    )
    provider_envelope_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_document_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    source_document_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_document_filename: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    completion_artifact_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The date the firm wants this signed by. Unlike expires_at, a passed
    # due date never invalidates the request -- it only means chase the client.
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminders: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enforce_signing_order: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # Immutable, PDF-bound signature placements (staff-reviewed, detected or
    # fallback) that the portal renders and the executed copy is stamped at.
    positioned_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Summary of the placement plan computed at creation (fill support,
    # signature field count, where placements came from) so list views do not
    # have to re-read the PDF from storage.
    signing_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The filled, signed and flattened PDF filed to the matter on completion.
    executed_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    # A signed copy the client uploaded, awaiting staff acceptance.
    submitted_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why the last attempt to file the executed copy failed (staff-facing) and
    # when it was tried, so the scheduler can retry without hammering storage.
    completion_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    declined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )

    signers: Mapped[list["SignatureSigner"]] = relationship(
        "SignatureSigner",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="SignatureSigner.sign_order",
    )


class SignatureSigner(Base):
    __tablename__ = "signature_signers"
    __table_args__ = (Index("ix_signature_signers_request_id", "request_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signature_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(
        String(100), default="signer", server_default="signer"
    )
    sign_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # pending | signed | declined
    status: Mapped[str] = mapped_column(
        String(50), default="pending", server_default="pending"
    )
    signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    signed_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    typed_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    declined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # ``{field_id: value}`` the signer entered in the in-document form.
    field_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # portal_inline | uploaded_copy (older rows carry "portal_typed" in audit)
    method: Mapped[str | None] = mapped_column(String(40), nullable=True)

    request: Mapped["SignatureRequest"] = relationship(
        "SignatureRequest", back_populates="signers"
    )
