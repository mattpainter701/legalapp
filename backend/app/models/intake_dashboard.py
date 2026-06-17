"""Models for receptionist intake dashboard history and assignment rotation."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LegacyCallRecord(Base):
    """Imported historical call row kept separate from active CRM records."""

    __tablename__ = "legacy_call_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_row_id",
            name="uq_legacy_call_records_source",
        ),
        Index("idx_legacy_call_records_tenant", "tenant_id"),
        Index("idx_legacy_call_records_phone", "tenant_id", "normalized_phone"),
        Index("idx_legacy_call_records_name", "tenant_id", "caller_name"),
        Index("idx_legacy_call_records_call_date", "tenant_id", "call_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_system: Mapped[str] = mapped_column(
        String(100), nullable=False, default="legacy_csv", server_default="legacy_csv"
    )
    source_row_id: Mapped[str] = mapped_column(String(200), nullable=False)

    caller_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    caller_phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    normalized_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    practice_area: Mapped[str | None] = mapped_column(String(100), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    prior_attorney_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    call_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    imported_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class PartnerRotationState(Base):
    """Per-practice next-in-line assignment configuration."""

    __tablename__ = "partner_rotation_state"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "practice_area",
            name="uq_partner_rotation_state_tenant_practice",
        ),
        Index("idx_partner_rotation_state_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    practice_area: Mapped[str] = mapped_column(String(100), nullable=False)
    eligible_user_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    last_assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
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
