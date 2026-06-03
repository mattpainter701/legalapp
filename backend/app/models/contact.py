"""SQLAlchemy models for contacts (clients, parties, leads) and intake pipeline."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Contact(Base):
    """Person or organization — client, opposing party, expert, vendor, etc."""

    __tablename__ = "contacts"
    __table_args__ = (
        Index("idx_contacts_tenant_id", "tenant_id"),
        Index("idx_contacts_tenant_email", "tenant_id", "email"),
        Index("idx_contacts_tenant_last_name", "tenant_id", "last_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # "person" | "organization"
    entity_type: Mapped[str] = mapped_column(
        String(50), default="person", server_default="person"
    )
    # "client" | "opposing_party" | "witness" | "expert" | "vendor" | "referral" | "other"
    contact_type: Mapped[str] = mapped_column(
        String(50), default="client", server_default="client"
    )

    # Person fields
    first_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Organization fields
    organization_name: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Contact info
    email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    secondary_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Address as JSON: {street, city, state, zip, country}
    address: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @property
    def display_name(self) -> str:
        if self.entity_type == "organization" and self.organization_name:
            return self.organization_name
        parts = [p for p in [self.first_name, self.last_name] if p]
        return " ".join(parts) if parts else self.email or str(self.id)


class Lead(Base):
    """Intake pipeline entry — a prospective client progressing from inquiry to matter."""

    __tablename__ = "leads"
    __table_args__ = (
        Index("idx_leads_tenant_id", "tenant_id"),
        Index("idx_leads_contact_id", "contact_id"),
        Index("idx_leads_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )

    # "new" | "contacted" | "qualified" | "conflict_checked" | "engaged" | "matter_opened" | "declined"
    status: Mapped[str] = mapped_column(
        String(50), default="new", server_default="new"
    )

    # "referral" | "website" | "cold_call" | "existing_client" | "bar_referral" | "other"
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)

    practice_area: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_value: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # "not_run" | "cleared" | "conflict_found"
    conflict_check_status: Mapped[str] = mapped_column(
        String(50), default="not_run", server_default="not_run"
    )
    conflict_check_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set when converted to a matter
    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="SET NULL"),
        nullable=True,
    )
    declined_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )
