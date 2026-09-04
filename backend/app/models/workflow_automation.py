"""Approval-gated rules that plan bounded matter workflow runs automatically.

A rule is a control-plane row: one bounded trigger, optional bounded matter
conditions, and one approved workflow template. Dispatch evidence is
append-only and protected by database triggers in the matching Alembic
migration; ORM conventions are not the audit boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


TRIGGER_EVENTS = ("matter_created", "matter_stage_changed")
RULE_STATUSES = ("draft", "active", "archived")
DISPATCH_OUTCOMES = ("planned", "blocked")


class MatterWorkflowAutomationRule(Base):
    """A firm-defined trigger that plans a reviewable workflow run."""

    __tablename__ = "matter_workflow_automation_rules"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_matter_workflow_automation_rules_tenant_id"
        ),
        CheckConstraint(
            "trigger_event IN ('matter_created', 'matter_stage_changed')",
            name="ck_matter_workflow_automation_rules_event",
        ),
        CheckConstraint(
            "(trigger_event = 'matter_stage_changed') = (trigger_stage IS NOT NULL)",
            name="ck_matter_workflow_automation_rules_stage",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_matter_workflow_automation_rules_status",
        ),
        CheckConstraint(
            "(status = 'active') = "
            "(activated_by_user_id IS NOT NULL AND activated_at IS NOT NULL)",
            name="ck_matter_workflow_automation_rules_activation",
        ),
        CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)",
            name="ck_matter_workflow_automation_rules_archival",
        ),
        CheckConstraint(
            "definition_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_matter_workflow_automation_rules_hash",
        ),
        CheckConstraint(
            "char_length(btrim(name)) > 0",
            name="ck_matter_workflow_automation_rules_name",
        ),
        CheckConstraint(
            "(trigger_stage IS NULL OR char_length(btrim(trigger_stage)) > 0) "
            "AND (match_matter_type IS NULL "
            "OR char_length(btrim(match_matter_type)) > 0) "
            "AND (match_practice_area IS NULL "
            "OR char_length(btrim(match_practice_area)) > 0)",
            name="ck_matter_workflow_automation_rules_trimmed",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            ["matter_workflow_templates.tenant_id", "matter_workflow_templates.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "activated_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "uq_matter_workflow_automation_rules_name",
            "tenant_id",
            func.lower(func.btrim(text("name"))),
            unique=True,
            postgresql_where=text("status <> 'archived'"),
        ),
        # Two identical active rules would plan two runs from one event.
        Index(
            "uq_matter_workflow_automation_rules_active_trigger",
            "tenant_id",
            "trigger_event",
            text("coalesce(lower(btrim(trigger_stage)), '')"),
            text("coalesce(lower(btrim(match_matter_type)), '')"),
            text("coalesce(lower(btrim(match_practice_area)), '')"),
            "template_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_matter_workflow_automation_rules_dispatch",
            "tenant_id",
            "trigger_event",
            "status",
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
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(40), nullable=False)
    trigger_stage: Mapped[str | None] = mapped_column(String(200))
    match_matter_type: Mapped[str | None] = mapped_column(String(100))
    match_practice_area: Mapped[str | None] = mapped_column(String(200))
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    definition_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    activated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MatterWorkflowAutomationEvent(Base):
    """Append-only evidence that one rule matched one matter event once."""

    __tablename__ = "matter_workflow_automation_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "rule_id",
            "dedupe_key",
            name="uq_matter_workflow_automation_events_dedupe",
        ),
        CheckConstraint(
            "outcome IN ('planned', 'blocked')",
            name="ck_matter_workflow_automation_events_outcome",
        ),
        CheckConstraint(
            "(outcome = 'planned') = (run_id IS NOT NULL)",
            name="ck_matter_workflow_automation_events_run",
        ),
        CheckConstraint(
            "trigger_event IN ('matter_created', 'matter_stage_changed')",
            name="ck_matter_workflow_automation_events_event",
        ),
        CheckConstraint(
            "dedupe_key ~ '^[a-f0-9]{64}$' AND rule_sha256 ~ '^[a-f0-9]{64}$' "
            "AND evidence_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_matter_workflow_automation_events_hashes",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [
                "matter_workflow_automation_rules.tenant_id",
                "matter_workflow_automation_rules.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["matter_workflow_runs.tenant_id", "matter_workflow_runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_matter_workflow_automation_events_matter",
            "tenant_id",
            "matter_id",
            "created_at",
        ),
        Index(
            "ix_matter_workflow_automation_events_rule",
            "tenant_id",
            "rule_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(40), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rule_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    detail_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
