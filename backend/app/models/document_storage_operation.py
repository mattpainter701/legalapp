"""Durable provider operations for tenant-owned document storage."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocumentStorageOperation(Base):
    """Idempotent evidence for one cloud storage write or reconciliation."""

    __tablename__ = "document_storage_operations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_document_storage_operations_tenant_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_doc_storage_ops_tenant_idempotency",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            ["generated_artifacts.tenant_id", "generated_artifacts.id"],
            name="fk_doc_storage_ops_artifact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "artifact_revision_id"],
            [
                "generated_artifact_revisions.tenant_id",
                "generated_artifact_revisions.artifact_id",
                "generated_artifact_revisions.id",
            ],
            name="fk_doc_storage_ops_artifact_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["matter_documents.tenant_id", "matter_documents.id"],
            name="fk_doc_storage_ops_document",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation_type IN ('create', 'update', 'verify', 'reconcile')",
            name="ck_doc_storage_ops_type",
        ),
        CheckConstraint(
            "status IN ('planned', 'writing', 'provider_accepted', 'verified', "
            "'linked', 'failed', 'ambiguous')",
            name="ck_doc_storage_ops_status",
        ),
        CheckConstraint(
            "delivery_certainty IS NULL OR delivery_certainty IN "
            "('unknown', 'not_delivered', 'provider_accepted', 'verified')",
            name="ck_doc_storage_ops_certainty",
        ),
        CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_doc_storage_ops_sha256",
        ),
        CheckConstraint(
            "content_size IS NULL OR content_size >= 0",
            name="ck_doc_storage_ops_content_size",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_doc_storage_ops_attempts",
        ),
        CheckConstraint(
            "length(btrim(idempotency_key)) > 0",
            name="ck_doc_storage_ops_idempotency_key",
        ),
        Index(
            "ix_doc_storage_ops_tenant_status",
            "tenant_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_doc_storage_ops_tenant_document",
            "tenant_id",
            "document_id",
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
    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="SET NULL"),
        nullable=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    artifact_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(25), nullable=False, default="planned", server_default="planned"
    )
    delivery_certainty: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_backend: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_drive_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    target_parent_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_object_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_etag: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_version_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )
