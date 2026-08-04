"""SQLAlchemy model for tasks and deadlines."""

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
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
        Index(
            "idx_tasks_tenant_status_assignee",
            "tenant_id",
            "status",
            "assigned_to_user_id",
        ),
        Index("idx_tasks_tenant_status_due", "tenant_id", "status", "due_date"),
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
    # "pending" | "in_progress" | "waiting" | "review" | "completed" | "cancelled"
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
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Read receipt: first time the assignee opened/saw this task in-app.
    viewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Follow-up outcome: when the assignee reported contacting the customer.
    customer_contacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "call" | "email" | "sms" | "meeting" | "other"
    customer_contact_method: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    # Why the task was completed/cancelled, and by whom. Cleared on reopen.
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Work-board workflow metadata. The legal due date remains independent of
    # these fields: moving a card never reschedules the work.
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        nullable=False,
    )
    waiting_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    waiting_follow_up_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Incremented for every mutation that can make a board card stale.
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
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


class TaskEvent(Base):
    """Append-only, tenant-scoped history for a task's internal lifecycle."""

    __tablename__ = "task_events"
    __table_args__ = (
        Index(
            "idx_task_events_tenant_task_created", "tenant_id", "task_id", "created_at"
        ),
        Index(
            "idx_task_events_tenant_type_created",
            "tenant_id",
            "event_type",
            "created_at",
        ),
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
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        nullable=False,
    )
