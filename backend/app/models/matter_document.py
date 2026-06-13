"""SQLAlchemy model for matter file attachments (case documents)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
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
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
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
        """Best-effort storage backend label derived from the stored location."""
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
