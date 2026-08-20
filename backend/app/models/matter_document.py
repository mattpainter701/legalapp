"""SQLAlchemy model for matter file attachments (case documents)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class MatterDocument(Base):
    """Case file attachment — uploaded documents tied to a matter."""

    __tablename__ = "matter_documents"

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
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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
    storage_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
