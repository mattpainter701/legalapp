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
    UniqueConstraint,
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

    # "manual" | "email_agent" | "calendar_sync" | "assistant"
    source: Mapped[str] = mapped_column(
        String(50), default="manual", server_default="manual"
    )
    external_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Deterministic follow-through the board runs when this task is approved out
    # of Review, e.g. ``{"type": "email_client", "to": [...], "body": ...}``.
    # The assistant may draft the payload but never executes it: approval is a
    # human transition, and execution is a plain hook with no model in the path.
    # ``TaskAutomationRun`` permits one automatic attempt per approval key.
    pending_action: Mapped[dict | None] = mapped_column(JSON, nullable=True)

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


class TaskAutomationRun(Base):
    """One attempt to execute a task's ``pending_action``.

    Existence of a row is the claim on the work. The unique constraint on
    ``(task_id, idempotency_key)`` prevents a replayed webhook, double-clicked
    Approve, or concurrent transition from starting a second automatic attempt.
    It cannot prove exactly-once external delivery after an ambiguous provider
    timeout, so those outcomes remain terminal for attorney review.
    """

    __tablename__ = "task_automation_runs"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "idempotency_key", name="uq_task_automation_runs_task_key"
        ),
        Index(
            "idx_task_automation_runs_tenant_status",
            "tenant_id",
            "status",
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
    # Matches ``pending_action["type"]``, kept denormalized so an operator can
    # audit what ran without rehydrating a possibly-cleared task payload.
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    # Immutable copy of exactly what the attorney approved. The task payload is
    # cleared after a confirmed send, but legal audit evidence must remain.
    action_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    action_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "queued" -> "sending" -> "sent" | "failed"
    #
    # Distinguishing queued from sending matters to the attorney: "we have not
    # tried yet" and "we tried and do not know the outcome" are different states,
    # and only "sent" means the client was actually contacted.
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delivery_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Distinguishes a confirmed no-send (safe to retry) from a transport
    # interruption where the provider may have accepted the message.
    delivery_certainty: Mapped[str | None] = mapped_column(String(30), nullable=True)
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
