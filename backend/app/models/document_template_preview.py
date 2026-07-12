"""Persisted, value-bound evidence for binary PDF template previews."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocumentTemplatePreview(Base):
    """Value-redacted proof that a user rendered one exact PDF input contract.

    Raw field values never belong in this table.  The contract hash and keyed
    value digest bind activation/save decisions to the source, field map, matter
    context, flattening mode, and exact reviewed values without duplicating
    customer document data or enabling offline guesses of low-entropy values.
    Reconciliation-only columns may hold bounded provider IDs, an output
    filename, or a tenant-scoped local path; FORCE RLS protects that operational
    metadata and it is populated only after a storage/database divergence.
    """

    __tablename__ = "document_template_previews"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('draft', 'activation', 'generation')",
            name="ck_document_template_previews_purpose",
        ),
        CheckConstraint(
            "reviewed_field_count >= 0 AND nonblank_field_count >= 0 "
            "AND nonblank_field_count <= reviewed_field_count",
            name="ck_document_template_previews_field_counts",
        ),
        CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND reconciliation_required_at IS NOT NULL "
            "AND reconciliation_resolved_at IS NULL)",
            name="ck_document_template_previews_terminal_state",
        ),
        CheckConstraint(
            "(reconciliation_required_at IS NULL AND reconciliation_reason IS NULL) "
            "OR (reconciliation_required_at IS NOT NULL AND reconciliation_reason "
            "IN ('cleanup_failed', 'commit_outcome_unknown'))",
            name="ck_document_template_previews_reconciliation_reason",
        ),
        CheckConstraint(
            "(reconciliation_resolved_at IS NULL AND reconciliation_resolution IS NULL) "
            "OR (reconciliation_resolved_at IS NOT NULL AND "
            "reconciliation_required_at IS NOT NULL AND "
            "reconciliation_resolution IS NOT NULL)",
            name="ck_document_template_previews_reconciliation_resolution",
        ),
        Index(
            "idx_document_template_previews_lookup",
            "tenant_id",
            "template_id",
            "previewed_by_user_id",
            "purpose",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    previewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="SET NULL"),
        nullable=True,
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    contract_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    values_hmac_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(50), nullable=False)
    flatten_pdf: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    reviewed_field_count: Mapped[int] = mapped_column(Integer, nullable=False)
    nonblank_field_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_field_names: Mapped[list] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_by_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    reconciliation_required_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reconciliation_storage_backend: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    reconciliation_provider_item_id: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    reconciliation_provider_drive_id: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    reconciliation_local_path: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )
    reconciliation_output_filename: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    reconciliation_output_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    reconciliation_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reconciliation_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_resolution: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
