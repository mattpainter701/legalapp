"""Immutable evidence linking observed practice to ordinary draft configuration."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkflowConfigurationProposal(Base):
    __tablename__ = "workflow_configuration_proposals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_workflow_configuration_proposals_tenant"
        ),
        UniqueConstraint(
            "tenant_id",
            "pattern_key",
            "proposal_sha256",
            name="uq_workflow_configuration_proposals_basis",
        ),
        CheckConstraint(
            "status IN ('pending','rejected')",
            name="ck_workflow_configuration_proposals_status",
        ),
        CheckConstraint(
            "pattern_key ~ '^[a-f0-9]{64}$' AND proposal_sha256 ~ '^[a-f0-9]{64}$' AND definition_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_workflow_configuration_proposals_hashes",
        ),
        CheckConstraint(
            "(status='pending' AND rejected_by_user_id IS NULL AND rejected_at IS NULL AND rejection_reason IS NULL) OR (status='rejected' AND rejected_by_user_id IS NOT NULL AND rejected_at IS NOT NULL AND rejection_reason IS NOT NULL AND length(btrim(rejection_reason))>0)",
            name="ck_workflow_configuration_proposals_rejection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            ["matter_workflow_templates.tenant_id", "matter_workflow_templates.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_version_id"],
            [
                "matter_workflow_template_versions.tenant_id",
                "matter_workflow_template_versions.id",
            ],
            ondelete="RESTRICT",
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
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rejected_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_job_id"],
            ["durable_jobs.tenant_id", "durable_jobs.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_workflow_configuration_proposals_tenant_created",
            "tenant_id",
            "created_at",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    pattern_key: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    configuration_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    baseline_json: Mapped[dict | None] = mapped_column(JSONB)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    rejected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
