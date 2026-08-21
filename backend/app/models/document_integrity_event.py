"""Tamper-evident, append-only evidence for cloud document workflows."""

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
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocumentIntegrityEvent(Base):
    """One metadata-only link in a tenant-scoped integrity hash chain."""

    __tablename__ = "document_integrity_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_document_integrity_events_tenant_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "event_hash",
            name="uq_document_integrity_events_tenant_hash",
        ),
        UniqueConstraint(
            "tenant_id",
            "chain_position",
            name="uq_document_integrity_events_tenant_chain_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            ["generated_artifacts.tenant_id", "generated_artifacts.id"],
            name="fk_doc_integrity_events_artifact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "artifact_revision_id"],
            [
                "generated_artifact_revisions.tenant_id",
                "generated_artifact_revisions.artifact_id",
                "generated_artifact_revisions.id",
            ],
            name="fk_doc_integrity_events_artifact_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["matter_documents.tenant_id", "matter_documents.id"],
            name="fk_doc_integrity_events_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "operation_id"],
            [
                "document_storage_operations.tenant_id",
                "document_storage_operations.id",
            ],
            name="fk_doc_integrity_events_operation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(btrim(event_type)) > 0",
            name="ck_doc_integrity_events_type",
        ),
        CheckConstraint(
            "actor_type IN ('user', 'service', 'provider', 'system')",
            name="ck_doc_integrity_events_actor_type",
        ),
        CheckConstraint(
            "chain_position > 0",
            name="ck_doc_integrity_events_chain_position",
        ),
        CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_doc_integrity_events_sha256",
        ),
        CheckConstraint(
            "prev_event_hash IS NULL OR prev_event_hash ~ '^[0-9a-f]{64}$'",
            name="ck_doc_integrity_events_prev_hash",
        ),
        CheckConstraint(
            "event_hash ~ '^[0-9a-f]{64}$'",
            name="ck_doc_integrity_events_hash",
        ),
        Index(
            "ix_doc_integrity_events_tenant_document_created",
            "tenant_id",
            "document_id",
            "created_at",
        ),
        Index(
            "ix_doc_integrity_events_tenant_created",
            "tenant_id",
            "created_at",
            "id",
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
    operation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_object_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_etag: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_version_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    chain_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prev_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
