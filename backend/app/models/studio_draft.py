"""Tenant-isolated, revision-safe persistence for Template Studio drafts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StudioSourceArtifact(Base):
    """Append-only, integrity-checked source bytes with an internal resolver binding."""

    __tablename__ = "studio_source_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            "sha256",
            "media_type",
            name="uq_studio_source_artifacts_contract",
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name="ck_studio_source_artifacts_hash"
        ),
        CheckConstraint("byte_size > 0", name="ck_studio_source_artifacts_size"),
        CheckConstraint(
            "byte_size = octet_length(content_bytes)",
            name="ck_studio_source_artifacts_byte_count",
        ),
        CheckConstraint(
            "resolver_key ~ '^studio-db:v1:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'",
            name="ck_studio_source_artifacts_resolver",
        ),
        UniqueConstraint("resolver_key", name="uq_studio_source_artifacts_resolver"),
        UniqueConstraint(
            "tenant_id",
            "sha256",
            "media_type",
            name="uq_studio_source_artifacts_content",
        ),
        Index("ix_studio_source_artifacts_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resolver_key: Mapped[str] = mapped_column(String(80), nullable=False)
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default="now()"
    )


class StudioDraft(Base):
    """Mutable head of a versioned template design.

    ``source_artifact_id`` is deliberately an application-owned opaque UUID.
    Workers resolve it through the studio source-reader service; provider paths,
    item IDs, and signed URLs never cross this persistence boundary.
    """

    __tablename__ = "studio_drafts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_studio_drafts_tenant_id"),
        CheckConstraint("revision > 0", name="ck_studio_drafts_revision"),
        CheckConstraint(
            "lifecycle_state IN ('active', 'archived')",
            name="ck_studio_drafts_lifecycle",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_artifact_id", "source_sha256", "source_media_type"],
            [
                "studio_source_artifacts.tenant_id",
                "studio_source_artifacts.id",
                "studio_source_artifacts.sha256",
                "studio_source_artifacts.media_type",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' AND identity_sha256 ~ '^[0-9a-f]{64}$' "
            "AND (published_base_sha256 IS NULL OR published_base_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_studio_drafts_hashes",
        ),
        Index("ix_studio_drafts_tenant_updated", "tenant_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    published_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_templates.id", ondelete="SET NULL")
    )
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    published_base_sha256: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    format: Mapped[str] = mapped_column(String(30), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    identity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_revision: Mapped[int | None] = mapped_column(Integer)
    evidence_invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    evidence_invalidation_reason: Mapped[str | None] = mapped_column(String(60))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default="now()",
        onupdate=_now,
    )


class StudioDraftField(Base):
    """Canonical field definition; its UUID survives automation-key renames."""

    __tablename__ = "studio_draft_fields"
    __table_args__ = (
        UniqueConstraint("tenant_id", "draft_id", "id", name="uq_studio_fields_scope"),
        UniqueConstraint("draft_id", "automation_key", name="uq_studio_fields_key"),
        CheckConstraint("position >= 0", name="ck_studio_fields_position"),
        ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="CASCADE",
        ),
        Index("ix_studio_fields_draft_position", "tenant_id", "draft_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    automation_key: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    field_type: Mapped[str] = mapped_column(String(40), nullable=False)
    required: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    definition: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )


class StudioDraftPlacement(Base):
    """Format-specific semantic anchor owned by Phase 2, not renderer geometry."""

    __tablename__ = "studio_draft_placements"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "draft_id", "id", name="uq_studio_placements_scope"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "draft_id", "field_id"],
            [
                "studio_draft_fields.tenant_id",
                "studio_draft_fields.draft_id",
                "studio_draft_fields.id",
            ],
            ondelete="CASCADE",
        ),
        Index("ix_studio_placements_field", "tenant_id", "draft_id", "field_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    format: Mapped[str] = mapped_column(String(30), nullable=False)
    anchor_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    anchor: Mapped[dict] = mapped_column(JSON, nullable=False)


class StudioDraftSnapshot(Base):
    """Immutable, content-addressed structural snapshot (never variable values/text)."""

    __tablename__ = "studio_draft_snapshots"
    __table_args__ = (
        UniqueConstraint("draft_id", "revision", name="uq_studio_snapshots_revision"),
        UniqueConstraint(
            "draft_id", "content_sha256", name="uq_studio_snapshots_content"
        ),
        CheckConstraint("revision > 0", name="ck_studio_snapshots_revision"),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_studio_snapshots_hash"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_studio_snapshots_draft_created", "tenant_id", "draft_id", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default="now()"
    )


class StudioDraftIdempotency(Base):
    __tablename__ = "studio_draft_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "operation",
            "idempotency_key",
            name="uq_studio_idempotency_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'", name="ck_studio_idempotency_hash"
        ),
        Index("ix_studio_idempotency_expires", "tenant_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default="now()"
    )


class StudioDraftAuditEvent(Base):
    """Redacted event ledger; detail contains identifiers/counts/reasons only."""

    __tablename__ = "studio_draft_audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_studio_audit_draft_created", "tenant_id", "draft_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    base_revision: Mapped[int | None] = mapped_column(Integer)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    detail: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default="now()"
    )
