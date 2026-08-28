"""Tenant agreement evidence and retention-control records.

Counsel-owned agreement text is published separately. These tables retain the
versioned document identity and an append-only tenant acceptance record.
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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgreementDefinition(Base):
    """An immutable, operator-published identity for one legal document."""

    __tablename__ = "agreement_definitions"
    __table_args__ = (
        UniqueConstraint("kind", "version", name="uq_agreement_kind_version"),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$' " "AND content_hash <> repeat('0', 64)",
            name="ck_agreement_definition_content_hash",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_agreement_definition_window",
        ),
        Index(
            "ix_agreement_definitions_effective",
            "required_for_onboarding",
            "effective_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    document_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    required_for_onboarding: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    counsel_owned: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    published_by_actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default="now()",
        nullable=False,
    )


class TenantAgreementAcceptance(Base):
    """Append-only evidence that an authorized tenant representative agreed."""

    __tablename__ = "tenant_agreement_acceptances"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "agreement_definition_id",
            name="uq_tenant_agreement_acceptance",
        ),
        Index("ix_tenant_agreement_acceptances_tenant", "tenant_id"),
        CheckConstraint(
            "document_hash ~ '^[0-9a-f]{64}$'",
            name="ck_tenant_acceptance_document_hash",
        ),
        CheckConstraint(
            "status <> 'accepted' OR authority_attested",
            name="ck_tenant_acceptance_authority",
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
    agreement_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agreement_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tenant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    document_version: Mapped[str] = mapped_column(String(40), nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    signer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    signer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    signer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    signer_title: Mapped[str] = mapped_column(String(255), nullable=False)
    authority_attested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attestation_text: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default="now()",
        nullable=False,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    auth_method: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="accepted", server_default="accepted", nullable=False
    )
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    esign_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    esign_envelope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_reference: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_retention_policy_tenant"),
        CheckConstraint("version >= 1", name="ck_retention_policy_version"),
        CheckConstraint(
            "NOT legal_hold OR legal_hold_reason IS NOT NULL",
            name="ck_retention_policy_legal_hold_reason",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    legal_hold: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    legal_hold_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_hold_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    policy_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default="now()",
        onupdate=_utcnow,
        nullable=False,
    )


class RetentionAction(Base):
    """Tenant-scoped audit row for policy changes, previews, and enforcement."""

    __tablename__ = "retention_actions"
    __table_args__ = (
        Index("ix_retention_actions_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_type: Mapped[str] = mapped_column(
        String(30), default="user", server_default="user", nullable=False
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="completed", server_default="completed", nullable=False
    )
    dry_run: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    legal_hold_at_execution: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default="now()",
        nullable=False,
    )
