"""Phase 3 tenant-isolated render artifact and preferred-evidence metadata."""

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
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StudioRenderArtifact(Base):
    """One immutable output-evidence row per materialized durable job."""

    __tablename__ = "studio_render_artifacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_studio_render_artifact_tenant_id"),
        UniqueConstraint("tenant_id", "job_id", name="uq_studio_render_artifact_job"),
        UniqueConstraint(
            "tenant_id",
            "id",
            "job_id",
            "draft_id",
            "revision",
            "identity_sha256",
            "evidence_basis_sha256",
            name="uq_studio_render_artifact_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["durable_jobs.tenant_id", "durable_jobs.id"],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_job_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_draft_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["studio_draft_snapshots.tenant_id", "studio_draft_snapshots.id"],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_snapshot_tenant",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "source_artifact_id",
                "source_sha256",
                "source_media_type",
                "source_format",
            ],
            [
                "studio_source_artifacts.tenant_id",
                "studio_source_artifacts.id",
                "studio_source_artifacts.sha256",
                "studio_source_artifacts.media_type",
                "studio_source_artifacts.format",
            ],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_source_contract",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_requester_tenant",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' "
            "AND effective_request_sha256 ~ '^[0-9a-f]{64}$' "
            "AND render_options_sha256 ~ '^[0-9a-f]{64}$' "
            "AND cache_key ~ '^[0-9a-f]{64}$' "
            "AND content_sha256 ~ '^[0-9a-f]{64}$' AND source_sha256 ~ '^[0-9a-f]{64}$' "
            "AND identity_sha256 ~ '^[0-9a-f]{64}$' AND snapshot_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_studio_render_artifact_hashes",
        ),
        CheckConstraint(
            "runtime_manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND geometry_manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND evidence_basis_sha256 ~ '^[0-9a-f]{64}$' "
            "AND (input_binding_sha256 IS NULL OR input_binding_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_studio_render_artifact_manifest_hashes",
        ),
        CheckConstraint(
            "(input_binding_sha256 IS NULL) = (input_binding_version IS NULL)",
            name="ck_studio_render_artifact_input_binding",
        ),
        CheckConstraint("revision > 0", name="ck_studio_render_artifact_revision"),
        CheckConstraint("byte_size > 0", name="ck_studio_render_artifact_size"),
        CheckConstraint(
            "artifact_page_count > 0 AND document_page_count >= artifact_page_count",
            name="ck_studio_render_artifact_pages",
        ),
        CheckConstraint(
            "(requested_page_range_start IS NULL) = (requested_page_range_end IS NULL) "
            "AND (requested_page_range_start IS NULL OR "
            "(requested_page_range_start > 0 AND requested_page_range_end >= requested_page_range_start))",
            name="ck_studio_render_artifact_requested_range",
        ),
        CheckConstraint(
            "artifact_kind IN ('analysis', 'ocr', 'page_preview', 'test_render')",
            name="ck_studio_render_artifact_kind",
        ),
        CheckConstraint(
            "adoption_outcome IN ('current_evidence', 'stale_output', 'cancelled_output')",
            name="ck_studio_render_adoption_outcome",
        ),
        CheckConstraint(
            "retention_class IN ('ephemeral', 'review', 'evidence')",
            name="ck_studio_render_retention_class",
        ),
        CheckConstraint(
            "storage_state IN ('active', 'delete_pending', 'deleted')",
            name="ck_studio_render_storage_state",
        ),
        CheckConstraint(
            "(retention_class = 'evidence' AND content_expires_at IS NULL "
            "AND metadata_expires_at IS NULL) OR "
            "(retention_class IN ('ephemeral', 'review') "
            "AND content_expires_at IS NOT NULL AND metadata_expires_at IS NOT NULL "
            "AND metadata_expires_at > content_expires_at)",
            name="ck_studio_render_artifact_temporary_expiry",
        ),
        CheckConstraint(
            "(artifact_kind = 'page_preview' AND requested_page_number IS NOT NULL "
            "AND artifact_page_count = 1) OR "
            "(artifact_kind != 'page_preview' AND requested_page_number IS NULL)",
            name="ck_studio_render_artifact_preview_page",
        ),
        CheckConstraint(
            "(storage_state = 'active' AND delete_requested_at IS NULL AND deleted_at IS NULL) OR "
            "(storage_state = 'delete_pending' AND delete_requested_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(storage_state = 'deleted' AND delete_requested_at IS NOT NULL AND deleted_at IS NOT NULL)",
            name="ck_studio_render_storage_lifecycle",
        ),
        Index("ix_studio_render_cache", "tenant_id", "cache_key", "created_at"),
        Index(
            "ix_studio_render_cleanup",
            "tenant_id",
            "storage_state",
            "retention_class",
            "content_expires_at",
        ),
        Index(
            "ix_studio_render_draft_revision",
            "tenant_id",
            "draft_id",
            "revision",
        ),
        Index(
            "ix_studio_render_object_state",
            "tenant_id",
            "object_key",
            "storage_state",
        ),
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
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_basis_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_format: Mapped[str] = mapped_column(String(20), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    render_options: Mapped[dict] = mapped_column(JSON, nullable=False)
    render_options_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_page_number: Mapped[int | None] = mapped_column(Integer)
    requested_page_range_start: Mapped[int | None] = mapped_column(Integer)
    requested_page_range_end: Mapped[int | None] = mapped_column(Integer)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    object_key: Mapped[str] = mapped_column(String(300), nullable=False)
    runtime_manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    runtime_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_binding_sha256: Mapped[str | None] = mapped_column(String(64))
    input_binding_version: Mapped[int | None] = mapped_column(Integer)
    artifact_page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    document_page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry_manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    geometry_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    adoption_outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    retention_class: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ephemeral", server_default="ephemeral"
    )
    storage_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    content_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legal_hold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delete_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default="now()"
    )


class StudioPreferredRenderEvidence(Base):
    """Exact live preferred-artifact identity for one tenant-bound draft."""

    __tablename__ = "studio_preferred_render_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="CASCADE",
            name="fk_studio_preferred_render_draft_tenant",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "artifact_id",
                "job_id",
                "draft_id",
                "revision",
                "identity_sha256",
                "evidence_basis_sha256",
            ],
            [
                "studio_render_artifacts.tenant_id",
                "studio_render_artifacts.id",
                "studio_render_artifacts.job_id",
                "studio_render_artifacts.draft_id",
                "studio_render_artifacts.revision",
                "studio_render_artifacts.identity_sha256",
                "studio_render_artifacts.evidence_basis_sha256",
            ],
            ondelete="RESTRICT",
            name="fk_studio_preferred_render_exact_evidence",
        ),
        CheckConstraint("revision > 0", name="ck_studio_preferred_render_revision"),
        CheckConstraint(
            "identity_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_studio_preferred_render_identity",
        ),
        UniqueConstraint(
            "tenant_id", "artifact_id", name="uq_studio_preferred_render_artifact"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    evidence_basis_sha256: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default="now()"
    )
