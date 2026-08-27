"""QBO (QuickBooks Online) integration models — OAuth2 tokens and item mappings."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class QBOIntegration(Base):
    """Per-tenant QuickBooks Online OAuth2 credential store.

    Reuses the encrypted-token pattern from TenantCredential.
    Access and refresh tokens are encrypted via Fernet (AES-256-GCM)
    before storage using the token_vault service.
    """

    __tablename__ = "qbo_integrations"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_qbo_integrations_tenant_id"),
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
    qbo_realm_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="QBO Company ID returned from OAuth"
    )
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    sync_frequency_minutes: Mapped[int] = mapped_column(
        Integer, default=15, server_default="15"
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_status: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="success, partial, failed"
    )
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sandbox_mode: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        comment="True when connected to QBO sandbox, false for production",
    )
    qbo_ar_account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    qbo_ar_account_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
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


class QBOItemMapping(Base):
    """Maps local billing source types to QuickBooks Online service Items.

    Each row binds a (source_type, expense_category) pair to a QBO Item so
    that synced invoices reference the correct income account in QBO.
    """

    __tablename__ = "qbo_item_mappings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_type",
            "expense_category",
            name="uq_qbo_item_mappings_tenant_type_category",
        ),
        Index("idx_qbo_item_mappings_tenant_id", "tenant_id"),
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
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="time_entry | expense | flat_fee | adjustment",
    )
    expense_category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Sub-type for expense rows (filing_fee, travel, etc.)",
    )
    qbo_item_id: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="QBO Item.Id"
    )
    qbo_item_name: Mapped[str] = mapped_column(String(200), nullable=False)
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
