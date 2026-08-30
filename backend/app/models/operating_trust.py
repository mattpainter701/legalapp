"""Auditable operating-trust workflows for the customer lifecycle.

The receipt and incident-update ledgers are append-only at the database layer.
Mutable support/offboarding cases carry workflow state; completed customer
lifecycle evidence is copied into immutable receipts rather than inferred from
the current case row.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    ForeignKeyConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CustomerLifecycleReceipt(Base):
    """Immutable evidence for onboarding, migration, export, and offboarding."""

    __tablename__ = "customer_lifecycle_receipts"
    __table_args__ = (
        Index("ix_lifecycle_receipts_tenant_created", "tenant_id", "created_at"),
        CheckConstraint(
            "receipt_type IN ('onboarding', 'migration', 'tenant_export', "
            "'offboarding', 'deletion')",
            name="ck_lifecycle_receipt_type",
        ),
        CheckConstraint(
            "status IN ('accepted', 'completed', 'requested', 'blocked')",
            name="ck_lifecycle_receipt_status",
        ),
        CheckConstraint(
            "receipt_hash ~ '^[0-9a-f]{64}$'",
            name="ck_lifecycle_receipt_hash",
        ),
        CheckConstraint(
            "artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_lifecycle_artifact_hash",
        ),
        CheckConstraint(
            "status NOT IN ('accepted', 'completed') OR authority_attested",
            name="ck_lifecycle_receipt_authority",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_type: Mapped[str] = mapped_column(String(40), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    expected_counts: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    actual_counts: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    discrepancies: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    source_import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_import_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    artifact_reference: Mapped[str | None] = mapped_column(String(1000))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    signer_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    signer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    signer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    signer_title: Mapped[str] = mapped_column(String(255), nullable=False)
    signer_actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    authority_attested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approvals_json: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    legal_hold_snapshot: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    provider_data_json: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    backup_expiry_json: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default="now()",
        nullable=False,
    )


class SupportRequest(Base):
    """Tenant-scoped support request with a policy-bound escalation clock."""

    __tablename__ = "support_requests"
    __table_args__ = (
        Index("ix_support_requests_tenant_created", "tenant_id", "created_at"),
        CheckConstraint("severity IN ('S1', 'S2', 'S3', 'S4')", name="ck_support_severity"),
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'mitigated', 'resolved')",
            name="ck_support_status",
        ),
        CheckConstraint(
            "escalation_level BETWEEN 0 AND 4", name="ck_support_escalation_level"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="open", server_default="open", nullable=False
    )
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    safe_summary: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    acknowledgement_objective_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    acknowledgement_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    escalation_level: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_by_email: Mapped[str] = mapped_column(String(320), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitigated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operator_actor_id: Mapped[str | None] = mapped_column(String(255))
    resolution_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default="now()",
        onupdate=_utcnow,
        nullable=False,
    )


class PublicIncident(Base):
    """Public-safe incident identity; progress is an append-only update stream."""

    __tablename__ = "public_incidents"
    __table_args__ = (
        Index("ix_public_incidents_started", "started_at"),
        CheckConstraint("severity IN ('S1', 'S2', 'S3')", name="ck_incident_severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    public_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(2), nullable=False)
    affected_services: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()", nullable=False
    )


class PublicIncidentUpdate(Base):
    __tablename__ = "public_incident_updates"
    __table_args__ = (
        Index("ix_incident_updates_incident_published", "incident_id", "published_at"),
        CheckConstraint(
            "state IN ('investigating', 'identified', 'monitoring', 'resolved')",
            name="ck_incident_update_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public_incidents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()", nullable=False
    )


class OffboardingCase(Base):
    """Non-destructive approval workflow preceding any tenant deletion action."""

    __tablename__ = "offboarding_cases"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_offboarding_case_tenant"),
        Index("ix_offboarding_cases_tenant_created", "tenant_id", "created_at"),
        CheckConstraint(
            "status IN ('requested', 'hold_blocked', 'approved', 'completed')",
            name="ck_offboarding_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    requested_scope: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    legal_hold_snapshot: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_by_email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default="now()",
        onupdate=_utcnow,
        nullable=False,
    )


class OffboardingApproval(Base):
    """Append-only, distinct-operator approval evidence."""

    __tablename__ = "offboarding_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["offboarding_cases.id", "offboarding_cases.tenant_id"],
            ondelete="RESTRICT",
            name="fk_offboarding_approval_case_tenant",
        ),
        UniqueConstraint("case_id", "actor_id", name="uq_offboarding_approval_actor"),
        Index("ix_offboarding_approvals_tenant_case", "tenant_id", "case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()", nullable=False
    )
