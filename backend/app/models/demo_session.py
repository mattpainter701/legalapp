"""Lifecycle and quota state for disposable sales-demo tenants."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


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
