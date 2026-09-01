"""SMB share model — a file share path scanned by an agent."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SmbShare(Base):
    __tablename__ = "smb_shares"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_smb_shares_tenant_id"),
        UniqueConstraint("agent_id", "share_path", name="uq_smb_shares_agent_path"),
        Index("ix_smb_shares_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("smb_agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Credential used to mount this share. Null means the agent falls back to
    # its locally configured identity (machine account / config.toml).
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("smb_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    share_path: Mapped[str] = mapped_column(String(500), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    file_extensions: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    max_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    scan_schedule: Mapped[str] = mapped_column(
        String(50), nullable=False, default="0 */6 * * *", server_default="0 */6 * * *"
    )
    last_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_scan_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_scan_file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_scan_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_verify_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_verify_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    exclude_patterns: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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
