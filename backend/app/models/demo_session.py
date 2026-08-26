"""Lifecycle and quota state for disposable sales-demo tenants."""

import uuid
import hashlib
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _resume_email_hash_default(context) -> str:
    email = str(context.get_current_parameters().get("prospect_email") or "")
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


class DemoSession(Base):
    __tablename__ = "demo_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('provisioning', 'active', 'expired', 'purging', 'purged', 'failed')",
            name="ck_demo_sessions_status",
        ),
        CheckConstraint("quota > 0", name="ck_demo_sessions_quota_positive"),
        CheckConstraint(
            "reserved >= 0 AND used >= 0 AND reserved + used <= quota",
            name="ck_demo_sessions_quota_counters",
        ),
        Index("idx_demo_sessions_status_expires", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    fixture_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    fixture_version: Mapped[str] = mapped_column(String(80), nullable=False)
    prospect_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prospect_email: Mapped[str] = mapped_column(String(255), nullable=False)
    resume_email_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, default=_resume_email_hash_default
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="provisioning",
        server_default="provisioning",
    )
    quota: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When a purge worker claimed this session.  Staleness has to be measured
    # from the claim, not from tenant expiry: a tenant that expired long before
    # its first purge attempt would otherwise look reclaimable the moment a
    # live worker claimed it.
    purge_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DemoUsageReservation(Base):
    __tablename__ = "demo_usage_reservations"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "idempotency_key", name="uq_demo_usage_session_key"
        ),
        CheckConstraint(
            "status IN ('reserved', 'settled', 'released')",
            name="ck_demo_usage_reservations_status",
        ),
        Index("idx_demo_usage_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("demo_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    surface: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="reserved", server_default="reserved"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
