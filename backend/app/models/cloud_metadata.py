"""Cloud metadata index model — lightweight routing/sync state, not document bodies."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CloudMetadata(Base):
    """Per-tenant index of cloud file/email metadata.

    Stores routing information (title, path, owner, timestamps, snippet)
    but NEVER stores full document content. Full content is fetched live
    from the provider API at query time.
    """

    __tablename__ = "cloud_metadata_index"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "object_type",
            "object_id",
            name="uq_cloud_metadata_tenant_provider_object",
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
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    object_type: Mapped[str] = mapped_column(String(20), nullable=False)
    object_id: Mapped[str] = mapped_column(String(500), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    participants: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    modified_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    web_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
