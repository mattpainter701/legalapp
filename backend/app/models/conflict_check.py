"""Saved, tenant-scoped conflict searches and immutable review evidence."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConflictCheckRecord(Base):
    __tablename__ = "conflict_checks"
    __table_args__ = (
        Index("idx_conflict_checks_tenant_created", "tenant_id", "created_at"),
        Index("idx_conflict_checks_matter", "matter_id"),
        Index("idx_conflict_checks_creator", "tenant_id", "created_by_user_id"),
        CheckConstraint("status IN ('open', 'closed')", name="ck_conflict_checks_status"),
        CheckConstraint(
            "decision IN ('needs_review', 'no_conflict_found', 'conflict_found', 'cleared_with_conditions')",
            name="ck_conflict_checks_decision",
        ),
        CheckConstraint("match_count >= 0", name="ck_conflict_checks_match_count"),
        CheckConstraint(
            "restricted_matter_count >= 0",
            name="ck_conflict_checks_restricted_count",
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
    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="SET NULL"),
        nullable=True,
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    query_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    restricted_matter_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open"
    )
    decision: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="needs_review",
        server_default="needs_review",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PortalInvoiceDownload(Base):
    """Metadata-only audit record for a client portal invoice PDF download."""

    __tablename__ = "portal_invoice_downloads"
    __table_args__ = (
        Index(
            "idx_portal_invoice_downloads_tenant_invoice",
            "tenant_id",
            "invoice_id",
            "downloaded_at",
        ),
        Index("idx_portal_invoice_downloads_invite", "invite_id"),
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
    matter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    invite_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("client_portal_invites.id", ondelete="SET NULL"),
        nullable=True,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False)
    branding_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
