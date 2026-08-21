"""Versioned matter work products created by chat or workspace automation."""

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
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GeneratedArtifact(Base):
    """Stable identity for a generated matter document across revisions."""

    __tablename__ = "generated_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "client_request_id",
            name="uq_generated_artifacts_tenant_request",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_generated_artifacts_tenant_id",
        ),
        CheckConstraint(
            "status IN ('draft', 'review', 'approved', 'filed', 'rejected', "
            "'superseded')",
            name="ck_generated_artifacts_status",
        ),
        CheckConstraint(
            "current_revision_no > 0",
            name="ck_generated_artifacts_revision_positive",
        ),
        CheckConstraint(
            "format IN ('docx', 'pdf', 'markdown')",
            name="ck_generated_artifacts_format",
        ),
        CheckConstraint(
            "source_channel IN ('matter_chat', 'workspace_mcp')",
            name="ck_generated_artifacts_source_channel",
        ),
        CheckConstraint(
            "length(btrim(kind)) > 0",
            name="ck_generated_artifacts_kind_nonempty",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_artifacts_request_sha256",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id", "current_revision_no"],
            [
                "generated_artifact_revisions.tenant_id",
                "generated_artifact_revisions.artifact_id",
                "generated_artifact_revisions.revision_no",
            ],
            name="fk_generated_artifacts_current_revision",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        Index(
            "idx_generated_artifacts_tenant_matter_updated",
            "tenant_id",
            "matter_id",
            "updated_at",
        ),
        Index("idx_generated_artifacts_tenant_task", "tenant_id", "task_id"),
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
    matter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matters.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    output_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("matter_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="draft", server_default="draft"
    )
    current_revision_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    source_channel: Mapped[str] = mapped_column(String(40), nullable=False)
    client_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
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


class GeneratedArtifactRevision(Base):
    """Immutable reviewed content and provenance for one artifact revision."""

    __tablename__ = "generated_artifact_revisions"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "revision_no",
            name="uq_generated_artifact_revisions_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "revision_no",
            name="uq_generated_artifact_revisions_tenant_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "id",
            name="uq_generated_artifact_revisions_tenant_artifact_id",
        ),
        CheckConstraint(
            "revision_no > 0",
            name="ck_generated_artifact_revisions_number_positive",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_artifact_revisions_content_sha256",
        ),
        CheckConstraint(
            "template_sha256 IS NULL OR template_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_artifact_revisions_template_sha256",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            ["generated_artifacts.tenant_id", "generated_artifacts.id"],
            name="fk_generated_artifact_revisions_tenant_artifact",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "parent_revision_id"],
            [
                "generated_artifact_revisions.tenant_id",
                "generated_artifact_revisions.artifact_id",
                "generated_artifact_revisions.id",
            ],
            name="fk_generated_artifact_revisions_parent",
            ondelete="RESTRICT",
        ),
        Index(
            "idx_generated_artifact_revisions_tenant_artifact",
            "tenant_id",
            "artifact_id",
            "revision_no",
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
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    template_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    variable_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    unresolved_variables: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    source_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    renderer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
