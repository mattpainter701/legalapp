"""Matter-SMB share join model — links matters to SMB shares with optional subfolder."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MatterSmbShare(Base):
    __tablename__ = "matter_smb_shares"
    __table_args__ = (
        UniqueConstraint(
            "matter_id", "share_id", "folder_path", name="uq_matter_smb_share"
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
    share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("smb_shares.id", ondelete="CASCADE"),
        nullable=False,
    )
    folder_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    display_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    auto_scan: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
