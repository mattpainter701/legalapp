"""SQLAlchemy model for communication history (email, call, meeting, letter, etc.)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class CommunicationLog(Base):
    """Record of any communication involving a contact or matter."""

    __tablename__ = "communication_logs"
    __table_args__ = (
        Index("idx_commlogs_tenant_id", "tenant_id"),
        Index("idx_commlogs_matter_id", "matter_id"),
        Index("idx_commlogs_contact_id", "contact_id"),
        Index("idx_commlogs_occurred_at", "tenant_id", "occurred_at"),
        Index("idx_commlogs_thread_ref", "tenant_id", "thread_ref"),
        Index(
            "uq_commlogs_zoom_phone_external_ref",
            "tenant_id",
            "external_ref",
            unique=True,
            postgresql_where=text("external_ref LIKE 'zoom_phone:call:%'"),
        ),
        Index(
            "uq_commlogs_teams_voice_external_ref",
            "tenant_id",
            "external_ref",
            unique=True,
            postgresql_where=text("external_ref LIKE 'teams_voice:%'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # "inbound" | "outbound"
    direction: Mapped[str] = mapped_column(
        String(20), default="outbound", server_default="outbound"
    )
    # "email" | "call" | "letter" | "meeting" | "portal" | "sms" | "other"
    channel: Mapped[str] = mapped_column(
        String(30), default="email", server_default="email"
    )
    # "logged" | "draft" | "sent" | "received" | "failed"
    status: Mapped[str] = mapped_column(
        String(30), default="logged", server_default="logged"
    )

    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="SET NULL"),
        nullable=True,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    # e.g. email message-id, Twilio call SID
    external_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Stored .eml attachment for captured email correspondence.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Provider conversation/thread id — groups captured emails into chains.
    thread_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # {"from": str, "to": [str], "cc": [str]} — who said what, without a join.
    participants: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

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
