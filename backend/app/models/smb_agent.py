"""SMB file share relay agent model — on-prem agent that scans file shares."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SmbAgent(Base):
    __tablename__ = "smb_agents"
    __table_args__ = (
        Index("ix_smb_agents_api_key_hash", "api_key_hash"),
        Index(
            "ix_smb_agents_tenant_status_expiry",
            "tenant_id",
            "status",
            "pairing_expires_at",
        ),
    )

    @property
    def is_registered(self) -> bool:
        """Distinguish a device enrollment from a one-time pairing reservation."""
        return self.api_key_hash != "pending"

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
    agent_name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    agent_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(200), nullable=True)
    os_info: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 19 characters: four groups of four from an unambiguous alphabet. The
    # previous token_urlsafe(16) code was 22 characters and did not fit.
    pairing_code: Mapped[str | None] = mapped_column(
        String(20), unique=True, nullable=True
    )
    pairing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    update_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="idle", server_default="idle"
    )
    update_target_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    update_manifest_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    update_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    update_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    update_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    update_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
