"""Named noninteractive principals, approved service rules, and occurrence evidence."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.workflow_run import RunIdentity, tenant_fk


class AutomationServiceIdentity(RunIdentity, Base):
    __tablename__ = "automation_service_identities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_automation_service_identity_tenant"
        ),
        UniqueConstraint(
            "tenant_id", "name", name="uq_automation_service_identity_name"
        ),
        UniqueConstraint("user_id", name="uq_automation_service_identity_user"),
        tenant_fk("user_id", "users"),
        tenant_fk("created_by_user_id", "users"),
        CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_automation_service_identity_status",
        ),
        CheckConstraint(
            "jsonb_typeof(capabilities)='array' AND jsonb_array_length(capabilities) BETWEEN 1 AND 10",
            name="ck_automation_service_identity_grant",
        ),
        CheckConstraint("version>0", name="ck_automation_service_identity_version"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class AutomationServiceRule(RunIdentity, Base):
    __tablename__ = "automation_service_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_automation_service_rule_tenant"),
        tenant_fk("identity_id", "automation_service_identities"),
        tenant_fk("matter_id", "matters"),
        tenant_fk("source_run_id", "workflow_runs"),
        tenant_fk("event_rule_id", "matter_workflow_automation_rules"),
        tenant_fk("created_by_user_id", "users"),
        tenant_fk("approved_by_user_id", "users"),
        CheckConstraint(
            "status IN ('draft','active','paused')",
            name="ck_automation_service_rule_status",
        ),
        CheckConstraint(
            "(status='draft')=(approved_by_user_id IS NULL AND approved_at IS NULL)",
            name="ck_automation_service_rule_approval",
        ),
        CheckConstraint(
            "(approved_by_user_id IS NULL)=(approved_at IS NULL)",
            name="ck_automation_service_rule_approval_pair",
        ),
        CheckConstraint(
            "definition_sha256 ~ '^[a-f0-9]{64}$' AND plan_sha256 ~ '^[a-f0-9]{64}$' AND payload_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_automation_service_rule_hashes",
        ),
        CheckConstraint(
            "(event_rule_id IS NULL)=(event_rule_sha256 IS NULL)",
            name="ck_automation_service_rule_event_pair",
        ),
        CheckConstraint("version>0", name="ck_automation_service_rule_version"),
        Index("ix_automation_service_rules_due", "tenant_id", "status", "created_at"),
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    schedule: Mapped[dict] = mapped_column(JSONB, nullable=False)
    plan_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False)
    plan_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_rule_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class AutomationServiceOccurrence(RunIdentity, Base):
    __tablename__ = "automation_service_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "rule_id",
            "occurrence_key",
            name="uq_automation_service_occurrence",
        ),
        UniqueConstraint("run_id", name="uq_automation_service_occurrence_run"),
        tenant_fk("rule_id", "automation_service_rules"),
        tenant_fk("identity_id", "automation_service_identities"),
        tenant_fk("run_id", "workflow_runs"),
        CheckConstraint(
            "outcome IN ('started','blocked','skipped')",
            name="ck_automation_service_occurrence_outcome",
        ),
        CheckConstraint(
            "(outcome='started')=(run_id IS NOT NULL)",
            name="ck_automation_service_occurrence_run",
        ),
        CheckConstraint(
            "rule_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_automation_service_occurrence_hash",
        ),
        Index(
            "ix_automation_service_occurrences_budget",
            "tenant_id",
            "identity_id",
            "created_at",
        ),
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    occurrence_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    rule_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(100))
