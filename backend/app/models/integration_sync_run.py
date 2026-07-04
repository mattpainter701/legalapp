"""Tenant-visible integration sync run history."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IntegrationSyncRun(Base):
    """Per-tenant provider/job sync result for admin health surfaces."""

    __tablename__ = "integration_sync_runs"
    __table_args__ = (
        Index(
            "idx_integration_sync_runs_tenant_provider_job",
            "tenant_id",
            "provider",
            "job_type",
            "started_at",
        ),
        Index("idx_integration_sync_runs_status", "status"),
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
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    items_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
