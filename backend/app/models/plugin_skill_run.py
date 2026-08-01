"""Persisted, reviewable work products created by add-on skills."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PluginSkillRun(Base):
    __tablename__ = "plugin_skill_runs"
    __table_args__ = (
        Index("idx_plugin_skill_runs_tenant_created", "tenant_id", "created_at"),
        Index("idx_plugin_skill_runs_matter_created", "matter_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matters.id", ondelete="SET NULL")
    )
    plugin_name: Mapped[str] = mapped_column(String(100), nullable=False)
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    memo: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    gates_triggered: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    model_used: Mapped[str] = mapped_column(String(255), nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requires_attorney_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    review_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="draft", server_default="draft"
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )
