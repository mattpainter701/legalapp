"""SQLAlchemy models for contacts (clients, parties, leads) and intake pipeline."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Contact(Base):
    """Person or organization — client, opposing party, expert, vendor, etc."""

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_contacts_tenant_id"),
        Index("idx_contacts_tenant_id", "tenant_id"),
        Index("idx_contacts_tenant_email", "tenant_id", "email"),
        Index("idx_contacts_tenant_last_name", "tenant_id", "last_name"),
        Index("idx_contacts_tenant_client_status", "tenant_id", "client_status"),
        Index("idx_contacts_tenant_client_account", "tenant_id", "client_account_id"),
        Index("idx_contacts_tenant_qbo_customer", "tenant_id", "qbo_customer_id"),
        Index(
            "uq_contacts_tenant_client_number",
            "tenant_id",
            "client_number",
            unique=True,
            postgresql_where=text("client_number IS NOT NULL"),
        ),
        CheckConstraint(
            "client_status IS NULL OR client_status IN "
            "('prospect', 'active', 'inactive', 'former')",
            name="ck_contacts_client_status",
        ),
        CheckConstraint(
            "preferred_contact_method IS NULL OR preferred_contact_method IN "
            "('email', 'phone', 'sms', 'mail', 'portal')",
            name="ck_contacts_preferred_contact_method",
        ),
        CheckConstraint(
            "preferred_payment_method IS NULL OR preferred_payment_method IN "
            "('stripe', 'check', 'ach', 'wire', 'cash', 'other')",
            name="ck_contacts_preferred_payment_method",
        ),
        CheckConstraint(
            "billing_delivery_method IN ('email', 'mail', 'portal')",
            name="ck_contacts_billing_delivery_method",
        ),
        CheckConstraint(
            "payment_terms_days BETWEEN 0 AND 365",
            name="ck_contacts_payment_terms_days",
        ),
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
    # "prospect" | "client" | "opposing_party" | "witness" | "expert" | "vendor" | "referral" | "other"
    contact_type: Mapped[str] = mapped_column(
        String(50), default="client", server_default="client"
    )

    # Person fields
    first_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    preferred_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Organization fields
    organization_name: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Contact info
    email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    secondary_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Address as JSON: {street, street2, city, state, zip, country}
    address: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Client relationship / CRM fields. These remain nullable so the shared
    # contact directory can still hold witnesses, experts, vendors, and other
    # non-client parties without fabricating client-specific data.
    client_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    client_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    client_since: Mapped[date | None] = mapped_column(Date, nullable=True)
    preferred_contact_method: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    preferred_contact_window: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    preferred_contact_timezone: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    preferred_language: Mapped[str | None] = mapped_column(String(100), nullable=True)
    emergency_contact: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sms_opt_in: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    sms_opt_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    email_opt_in: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    referral_source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_contacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # People attached to an organization client remain ordinary contacts, but
    # this link keeps them underneath one canonical CRM account instead of
    # inflating the client directory with duplicate top-level records.
    client_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optional durable portal identity for this canonical client contact.
    client_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_contact_role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_primary_client_contact: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    client_contact_authorization: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Billing preferences and external customer mappings. Provider IDs are
    # identifiers, not credentials; OAuth and API secrets remain in the
    # encrypted tenant integration vaults.
    preferred_payment_method: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    billing_delivery_method: Mapped[str] = mapped_column(
        String(50), default="email", server_default="email"
    )
    payment_terms_days: Mapped[int] = mapped_column(
        Integer, default=30, server_default="30"
    )
    billing_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    qbo_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    qbo_sync_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    qbo_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

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
        UniqueConstraint("tenant_id", "id", name="uq_leads_tenant_id"),
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
    status: Mapped[str] = mapped_column(String(50), default="new", server_default="new")

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
