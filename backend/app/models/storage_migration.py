"""Persistent state and audit rows for cloud-provider migrations."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StorageMigration(Base):
    __tablename__ = "storage_migrations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_storage_migrations_tenant_id"),
        CheckConstraint(
            "phase IN ('planning','reconciling','awaiting_confirmation','cutover','complete','abandoned')",
            name="ck_storage_migration_phase",
        ),
        Index("idx_storage_migrations_tenant_phase", "tenant_id", "phase"),
        Index(
            "uq_storage_migrations_one_active_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text(
                "phase IN ('planning','reconciling','awaiting_confirmation','cutover')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    target_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    phase: Mapped[str] = mapped_column(String(40), nullable=False, default="planning")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    evidence_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    acknowledged_policy: Mapped[str | None] = mapped_column(String(100), nullable=True)
    previous_root: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    target_root: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    bucket_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    needs_reindex: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class StorageMigrationMatch(Base):
    __tablename__ = "storage_migration_matches"
    __table_args__ = (
        Index(
            "idx_storage_migration_matches_migration_bucket", "migration_id", "bucket"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "migration_id"],
            ["storage_migrations.tenant_id", "storage_migrations.id"],
            ondelete="CASCADE",
            name="fk_storage_matches_tenant_migration",
        ),
        UniqueConstraint(
            "migration_id", "object_type", "object_id", name="uq_storage_match_object"
        ),
        CheckConstraint(
            "bucket IN ('matched','missing','ambiguous')",
            name="ck_storage_match_bucket",
        ),
        CheckConstraint(
            "object_type IN ('matter','document')", name="ck_storage_match_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    migration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    object_type: Mapped[str] = mapped_column(String(30), nullable=False)
    object_id: Mapped[str] = mapped_column(String(500), nullable=False)
    bucket: Mapped[str] = mapped_column(String(30), nullable=False)
    matching_rung: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    target_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class OnboardingRootAudit(Base):
    """Immutable record of cloud roots seen during onboarding re-entry."""

    __tablename__ = "onboarding_root_audits"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    root: Mapped[dict] = mapped_column(JSON, nullable=False)
    action: Mapped[str] = mapped_column(
        String(40), nullable=False, default="onboarding_rerun"
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


# Descriptive compatibility name used by migration/state integrations.
StorageMigrationState = StorageMigration
