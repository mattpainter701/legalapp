"""Metadata-only audit trail for Office add-in action plans."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OfficeActionRun(Base):
    """Tenant-scoped proof of an Office plan and its terminal client result.

    Raw document text, email bodies, replacement text, cell values, formulas,
    and user instructions are deliberately excluded.
    """

    __tablename__ = "office_action_runs"
    __table_args__ = (
        CheckConstraint(
            "surface IN ('word', 'excel', 'outlook')",
            name="ck_office_action_runs_surface",
        ),
        CheckConstraint(
            "status IN ('proposed', 'applied', 'rejected', 'stale', 'failed')",
            name="ck_office_action_runs_status",
        ),
        CheckConstraint(
            "action_count >= 0 AND context_size >= 0 AND "
            "(result_action_count IS NULL OR result_action_count >= 0)",
            name="ck_office_action_runs_counts",
        ),
        Index("idx_office_action_runs_tenant_created", "tenant_id", "created_at"),
        Index("idx_office_action_runs_user_created", "user_id", "created_at"),
        Index("idx_office_action_runs_plan", "tenant_id", "plan_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[str] = mapped_column(String(100), nullable=False)
    surface: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)
    action_types: Mapped[list] = mapped_column(JSON, nullable=False)
    action_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result_action_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_size: Mapped[int] = mapped_column(Integer, nullable=False)
    base_fingerprint_hmac_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    result_fingerprint_hmac_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    instruction_hmac_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="proposed", server_default="proposed"
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_alias: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
