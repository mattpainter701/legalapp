"""SQLAlchemy model for tasks and deadlines."""

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Task(Base):
    """Actionable item tied to a matter, contact, or standalone."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("idx_tasks_tenant_id", "tenant_id"),
        Index("idx_tasks_matter_id", "matter_id"),
        Index("idx_tasks_assigned_to", "tenant_id", "assigned_to_user_id"),
        Index("idx_tasks_due_date", "tenant_id", "due_date"),
        Index("idx_tasks_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "deadline" | "hearing" | "filing" | "deposition" | "call" | "follow_up" | "review" | "general"
    task_type: Mapped[str] = mapped_column(
        String(50), default="general", server_default="general"
    )
    # "pending" | "in_progress" | "completed" | "cancelled"
    status: Mapped[str] = mapped_column(
        String(50), default="pending", server_default="pending"
    )
    # "low" | "medium" | "high" | "urgent"
    priority: Mapped[str] = mapped_column(
        String(20), default="medium", server_default="medium"
    )

    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=True,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # "manual" | "email_agent" | "calendar_sync"
    source: Mapped[str] = mapped_column(
        String(50), default="manual", server_default="manual"
    )
    external_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)

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
