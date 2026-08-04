"""Persisted, tenant-scoped proposals for bounded matter-document revisions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MatterDocumentRevision(Base):
    """One immutable-source, review-before-approval DOCX revision proposal."""

    __tablename__ = "matter_document_revisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "client_request_id",
            name="uq_doc_revisions_tenant_client_request",
        ),
        UniqueConstraint(
            "tenant_id",
            "root_document_id",
            "version_no",
            name="uq_doc_revisions_root_version",
        ),
        UniqueConstraint(
            "output_document_id",
            name="uq_doc_revisions_output_document",
        ),
        CheckConstraint(
            "status IN ('processing', 'needs_input', 'ready_for_review', "
            "'approved', 'rejected', 'superseded', 'failed')",
            name="ck_doc_revisions_status",
        ),
        CheckConstraint("version_no > 0", name="ck_doc_revisions_version_positive"),
        CheckConstraint(
            "status NOT IN ('ready_for_review', 'approved', 'rejected', "
            "'superseded') OR "
            "(output_document_id IS NOT NULL AND output_sha256 IS NOT NULL)",
            name="ck_doc_revisions_output_required",
        ),
        CheckConstraint(
            "status <> 'approved' OR approved_at IS NOT NULL",
            name="ck_doc_revisions_approval_evidence",
        ),
        Index(
            "ix_doc_revisions_tenant_matter_created",
            "tenant_id",
            "matter_id",
            "created_at",
        ),
        Index(
            "ix_doc_revisions_tenant_root_version",
            "tenant_id",
            "root_document_id",
            "version_no",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    matter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
    )
    root_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_document_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    output_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    rejected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="processing", server_default="processing"
    )
    clarification_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    operations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    output_text_preview: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    requested_model_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    resolved_model_tier: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model_alias: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    prepared_esign_signature_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signature_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    prepared_esign_snapshot_hmac_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    prepared_esign_preview: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prepared_esign_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    prepared_esign_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )
