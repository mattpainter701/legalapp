"""Durable, bounded capability runs with encrypted inputs and immutable events."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def tenant_fk(column, table):
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{table}.tenant_id", f"{table}.id"],
        ondelete="RESTRICT",
    )


class RunIdentity:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class WorkflowRun(RunIdentity, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workflow_runs_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "request_id",
            name="uq_workflow_runs_actor_request",
        ),
        tenant_fk("actor_user_id", "users"),
        tenant_fk("matter_id", "matters"),
        CheckConstraint(
            "origin_channel IN ('matter_chat','workspace_mcp')",
            name="ck_workflow_runs_channel",
        ),
        CheckConstraint(
            "status IN ('queued','running','awaiting_input','awaiting_review','reconciliation_required','completed','cancelled','blocked','failed')",
            name="ck_workflow_runs_status",
        ),
        CheckConstraint(
            "version>0 AND next_step>=0 AND next_step<=12",
            name="ck_workflow_runs_position",
        ),
        CheckConstraint(
            "plan_sha256 ~ '^[a-f0-9]{64}$' AND request_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_workflow_runs_hashes",
        ),
        CheckConstraint(
            "(origin_channel='workspace_mcp')=(grant_id IS NOT NULL AND client_id IS NOT NULL)",
            name="ck_workflow_runs_grant",
        ),
        Index(
            "ix_workflow_runs_tenant_matter_created",
            "tenant_id",
            "matter_id",
            "created_at",
        ),
        Index("ix_workflow_runs_tenant_status", "tenant_id", "status", "created_at"),
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    origin_channel: Mapped[str] = mapped_column(String(30), nullable=False)
    grant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    client_id: Mapped[str | None] = mapped_column(String(200))
    scope_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
    objective: Mapped[str] = mapped_column(String(80), nullable=False)
    plan_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_context_ciphertext: Mapped[str | None] = mapped_column(Text)
    source_context_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="queued", server_default="queued"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    next_step: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failure_code: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRunStep(RunIdentity, Base):
    __tablename__ = "workflow_run_steps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workflow_run_steps_tenant_id"),
        UniqueConstraint(
            "tenant_id", "run_id", "position", name="uq_workflow_run_steps_position"
        ),
        UniqueConstraint(
            "tenant_id", "run_id", "step_key", name="uq_workflow_run_steps_key"
        ),
        tenant_fk("run_id", "workflow_runs"),
        tenant_fk("task_id", "tasks"),
        tenant_fk("artifact_id", "generated_artifacts"),
        tenant_fk("storage_operation_id", "document_storage_operations"),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "artifact_revision_id", "approval_id"],
            [
                "work_artifact_approval.tenant_id",
                "work_artifact_approval.artifact_id",
                "work_artifact_approval.revision_id",
                "work_artifact_approval.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "artifact_revision_id"],
            [
                "generated_artifact_revisions.tenant_id",
                "generated_artifact_revisions.artifact_id",
                "generated_artifact_revisions.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "position>=0 AND position<12 AND attempts>=0",
            name="ck_workflow_run_steps_position",
        ),
        CheckConstraint(
            "status IN ('pending','executing','awaiting_input','awaiting_review','completed','blocked','uncertain')",
            name="ck_workflow_run_steps_status",
        ),
        CheckConstraint(
            "arguments_sha256 ~ '^[a-f0-9]{64}$' AND (result_sha256 IS NULL OR result_sha256 ~ '^[a-f0-9]{64}$')",
            name="ck_workflow_run_steps_hashes",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    step_key: Mapped[str] = mapped_column(String(40), nullable=False)
    capability: Mapped[str] = mapped_column(String(100), nullable=False)
    references_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    arguments_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_ciphertext: Mapped[str | None] = mapped_column(Text)
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    result_summary: Mapped[dict | None] = mapped_column(JSONB)
    required_inputs: Mapped[list | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    artifact_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    storage_operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action_sha256: Mapped[str | None] = mapped_column(String(64))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRunEvent(RunIdentity, Base):
    __tablename__ = "workflow_run_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "run_id", "sequence", name="uq_workflow_run_events_sequence"
        ),
        tenant_fk("run_id", "workflow_runs"),
        tenant_fk("step_id", "workflow_run_steps"),
        tenant_fk("actor_user_id", "users"),
        CheckConstraint("sequence>0", name="ck_workflow_run_events_sequence"),
        Index("ix_workflow_run_events_run", "tenant_id", "run_id", "sequence"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
