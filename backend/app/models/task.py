"""SQLAlchemy model for tasks and deadlines."""

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
        UniqueConstraint("tenant_id", "id", name="uq_tasks_tenant_id"),
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
        Index(
            "idx_tasks_tenant_review_stage",
            "tenant_id",
            "review_policy",
            "review_stage",
        ),
        CheckConstraint(
            "review_policy IN ('single', 'staff_then_attorney')",
            name="ck_tasks_review_policy",
        ),
        CheckConstraint(
            "review_stage IN ('attorney', 'staff', 'attorney_pending', 'approved')",
            name="ck_tasks_review_stage",
        ),
        CheckConstraint(
            "review_policy != 'staff_then_attorney' OR "
            "(staff_reviewer_user_id IS NOT NULL AND "
            "attorney_reviewer_user_id IS NOT NULL)",
            name="ck_tasks_staged_reviewers_required",
        ),
        CheckConstraint(
            "review_policy != 'staff_then_attorney' OR "
            "staff_reviewer_user_id != attorney_reviewer_user_id",
            name="ck_tasks_staged_reviewers_distinct",
        ),
        CheckConstraint(
            "(staff_reviewed_at IS NULL) = "
            "(staff_reviewed_by_user_id IS NULL) AND "
            "(attorney_approved_at IS NULL) = "
            "(attorney_approved_by_user_id IS NULL)",
            name="ck_tasks_review_evidence_pairs",
        ),
        CheckConstraint(
            "staff_reviewed_by_user_id IS NULL OR "
            "staff_reviewed_by_user_id = staff_reviewer_user_id",
            name="ck_tasks_staff_reviewer_evidence_actor",
        ),
        CheckConstraint(
            "attorney_approved_by_user_id IS NULL OR attorney_approved_by_user_id = attorney_reviewer_user_id",
            name="ck_tasks_attorney_reviewer_evidence_actor",
        ),
        CheckConstraint(
            "review_policy != 'staff_then_attorney' OR review_stage != 'staff' OR "
            "(reviewer_user_id IS NOT NULL AND "
            "reviewer_user_id = staff_reviewer_user_id)",
            name="ck_tasks_staff_stage_reviewer",
        ),
        CheckConstraint(
            "review_policy != 'staff_then_attorney' OR "
            "review_stage != 'attorney_pending' OR "
            "(reviewer_user_id IS NOT NULL AND "
            "reviewer_user_id = attorney_reviewer_user_id)",
            name="ck_tasks_attorney_stage_reviewer",
        ),
        CheckConstraint(
            "review_policy != 'staff_then_attorney' OR review_stage != 'approved' OR "
            "attorney_override OR (staff_reviewed_at IS NOT NULL AND "
            "staff_reviewed_by_user_id = staff_reviewer_user_id)",
            name="ck_tasks_approved_staff_evidence",
        ),
        CheckConstraint(
            "review_policy != 'staff_then_attorney' OR "
            "review_stage != 'attorney_pending' OR "
            "(staff_reviewed_at IS NOT NULL AND "
            "staff_reviewed_by_user_id IS NOT NULL)",
            name="ck_tasks_staff_review_evidence",
        ),
        CheckConstraint(
            "review_policy != 'staff_then_attorney' OR "
            "review_stage != 'approved' OR "
            "(attorney_approved_at IS NOT NULL AND "
            "attorney_approved_by_user_id IS NOT NULL)",
            name="ck_tasks_attorney_approval_evidence",
        ),
        CheckConstraint(
            "NOT attorney_override OR "
            "(review_policy = 'staff_then_attorney' AND "
            "review_stage = 'approved' AND attorney_approved_at IS NOT NULL "
            "AND attorney_approved_by_user_id IS NOT NULL)",
            name="ck_tasks_attorney_override_evidence",
        ),
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
    review_policy: Mapped[str] = mapped_column(
        String(30), default="single", server_default="single", nullable=False
    )
    review_stage: Mapped[str] = mapped_column(
        String(30), default="attorney", server_default="attorney", nullable=False
    )
    staff_reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    attorney_reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    staff_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    staff_reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    attorney_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attorney_approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    attorney_override: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Incremented for every mutation that can make a board card stale.
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )

    # "manual" | "email_agent" | "email_subject_tag" | "calendar_sync" | "assistant"
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
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            name="fk_task_automation_runs_tenant_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sms_message_id"],
            ["sms_messages.tenant_id", "sms_messages.id"],
            name="fk_task_automation_runs_tenant_sms_message",
        ),
        Index(
            "idx_task_automation_runs_tenant_sms_message",
            "tenant_id",
            "sms_message_id",
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
    # "queued" -> "sending" -> "submitted" -> "sent" | "failed"
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
    # Migration 149 expands this audit value without an in-place type rewrite.
    # The legacy column remains mapped for rolling deploy/rollback compatibility;
    # a database trigger synchronizes it with the exact v2 vocabulary.
    _delivery_certainty_legacy: Mapped[str | None] = mapped_column(
        "delivery_certainty", String(30), nullable=True
    )
    _delivery_certainty_v2: Mapped[str | None] = mapped_column(
        "delivery_certainty_v2", String(50), nullable=True
    )

    @property
    def delivery_certainty(self) -> str | None:
        """Return exact v2 truth, falling back to a pre-149 legacy row."""
        value = self._delivery_certainty_v2 or self._delivery_certainty_legacy
        if value == "failed_after_acceptance":
            return "provider_failed_after_acceptance"
        return value

    @delivery_certainty.setter
    def delivery_certainty(self, value: str | None) -> None:
        """Dual-write the expand-phase columns with a legacy-safe alias."""
        canonical = (
            "provider_failed_after_acceptance"
            if value == "failed_after_acceptance"
            else value
        )
        self._delivery_certainty_v2 = canonical
        self._delivery_certainty_legacy = (
            "failed_after_acceptance"
            if canonical == "provider_failed_after_acceptance"
            else canonical
        )

    sms_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reconciliation_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
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
