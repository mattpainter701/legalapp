"""SQLAlchemy model for matter file attachments (case documents)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class MatterDocument(Base):
    """Case file attachment — uploaded documents tied to a matter."""

    __tablename__ = "matter_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_matter_documents_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "generated_artifact_revision_id",
            name="uq_matter_documents_tenant_artifact_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "generated_artifact_id"],
            ["generated_artifacts.tenant_id", "generated_artifacts.id"],
            name="fk_matter_documents_tenant_generated_artifact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "generated_artifact_id", "generated_artifact_revision_id"],
            [
                "generated_artifact_revisions.tenant_id",
                "generated_artifact_revisions.artifact_id",
                "generated_artifact_revisions.id",
            ],
            name="fk_matter_documents_tenant_generated_artifact_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "supersedes_document_id"],
            ["matter_documents.tenant_id", "matter_documents.id"],
            name="fk_matter_documents_tenant_supersedes",
            ondelete="RESTRICT",
        ),
        # RESTRICT, not SET NULL: a folder must never take its documents with
        # it. Callers re-file or detach the documents first, in the same
        # transaction as the folder delete.
        ForeignKeyConstraint(
            ["tenant_id", "folder_id"],
            ["matter_document_folders.tenant_id", "matter_document_folders.id"],
            name="fk_matter_documents_tenant_folder",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "document_role IS NULL OR document_role IN "
            "('source', 'working_copy', 'review_snapshot', 'filed_copy', 'export')",
            name="ck_matter_documents_document_role",
        ),
        CheckConstraint(
            "document_status IS NULL OR document_status IN "
            "('draft', 'in_review', 'approved', 'filed', 'superseded', 'archived')",
            name="ck_matter_documents_document_status",
        ),
        CheckConstraint(
            "storage_state IS NULL OR storage_state IN "
            "('untracked', 'pending', 'verified', 'conflict', 'deleted')",
            name="ck_matter_documents_storage_state",
        ),
        CheckConstraint(
            "document_sha256 IS NULL OR document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_matter_documents_document_sha256",
        ),
        Index(
            "idx_matter_documents_tenant_artifact_revision",
            "tenant_id",
            "generated_artifact_id",
            "generated_artifact_revision_id",
        ),
        Index(
            "idx_matter_documents_tenant_storage_state",
            "tenant_id",
            "storage_state",
        ),
        Index(
            "idx_matter_documents_tenant_matter_folder",
            "tenant_id",
            "matter_id",
            "folder_id",
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
    generated_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    generated_artifact_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    supersedes_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # NULL means the document sits at the matter root of the document explorer.
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    storage_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    _storage_backend: Mapped[str | None] = mapped_column(
        "storage_backend", String(50), nullable=True
    )
    provider_object_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_drive_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_parent_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_etag: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_version_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_checksum: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    storage_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    storage_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    document_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    storage_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Firm controls which case files are visible in the client portal.
    portal_visible: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @property
    def cloud_url(self) -> str | None:
        """External edit/view URL when this document is backed by customer cloud."""
        if self.storage_path and self.storage_path.startswith(("http://", "https://")):
            return self.storage_path
        return None

    @property
    def storage_backend(self) -> str:
        """Explicit backend when known, otherwise legacy URL-derived label."""
        explicit_backend = self._normalize_storage_backend(self._storage_backend)
        if explicit_backend:
            return explicit_backend

        explicit_provider = self._normalize_storage_backend(self.storage_provider)
        if explicit_provider:
            return explicit_provider

        cloud_url = self.cloud_url
        if not cloud_url:
            return "local"
        lowered = cloud_url.lower()
        if "drive.google.com" in lowered or "docs.google.com" in lowered:
            return "google_drive"
        if (
            "sharepoint.com" in lowered
            or "1drv.ms" in lowered
            or "onedrive.live.com" in lowered
        ):
            return "onedrive"
        return "cloud"

    @storage_backend.setter
    def storage_backend(self, value: str | None) -> None:
        self._storage_backend = value

    @staticmethod
    def _normalize_storage_backend(value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip().lower().replace("-", "_")
        if not normalized:
            return None
        aliases = {
            "google": "google_drive",
            "gdrive": "google_drive",
            "drive": "google_drive",
            "microsoft": "onedrive",
            "ms_graph": "onedrive",
            "one_drive": "onedrive",
            "share_point": "sharepoint",
        }
        return aliases.get(normalized, normalized)
