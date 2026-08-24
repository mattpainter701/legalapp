"""Tenant-scoped inbound email aliases and their review queue."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InboundEmailAlias(Base):
    """An opaque, rotatable inbound address mapped to one matter."""

    __tablename__ = "inbound_email_aliases"
    __table_args__ = (
        CheckConstraint("kind = 'matter'", name="ck_inbound_alias_kind"),
        CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_inbound_alias_status"
        ),
        Index("idx_inbound_aliases_tenant", "tenant_id"),
        Index("idx_inbound_aliases_matter", "tenant_id", "matter_id"),
        Index(
            "uq_inbound_alias_active_matter",
            "tenant_id",
            "matter_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
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
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="matter", server_default="matter"
    )
    # The lookup hash permits global routing without exposing the address token
    # in database indexes. The encrypted value is only decrypted for an
    # authenticated user in the owning tenant.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    encrypted_local_part: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class InboundEmail(Base):
    """A delivered raw email waiting for explicit filing or rejection."""

    __tablename__ = "inbound_emails"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_inbound_email_status",
        ),
        UniqueConstraint(
            "alias_id", "message_sha256", name="uq_inbound_email_alias_sha256"
        ),
        Index("idx_inbound_emails_tenant_status", "tenant_id", "status"),
        Index("idx_inbound_emails_matter_status", "matter_id", "status"),
        Index("idx_inbound_emails_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    alias_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inbound_email_aliases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    matter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    envelope_sender: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    participants: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    authentication_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    message_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    communication_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("communication_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
