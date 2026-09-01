"""Immutable customer-directory identity and authorization health state."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NativeIdentityMapping(Base):
    __tablename__ = "native_identity_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_native_identity_tenant_user"),
        UniqueConstraint(
            "tenant_id",
            "directory_tenant_id",
            "object_id",
            name="uq_native_identity_directory_object",
        ),
        Index("ix_native_identity_tenant_state", "tenant_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    directory_tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_sid: Mapped[str] = mapped_column(String(184), nullable=False)
    effective_sids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
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
