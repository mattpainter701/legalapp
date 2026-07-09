import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class TenantCredential(Base):
    __tablename__ = "tenant_credentials"
    # Production enforces this via the ix_tenant_credentials_tenant_provider
    # unique index (migration 009); declared here too so the test schema
    # (built via Base.metadata.create_all) gets the same protection.
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", name="uq_tenant_credentials_tenant_provider"
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
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    health: Mapped[str] = mapped_column(
        String(30), nullable=False, default="healthy", server_default="healthy"
    )
    last_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refresh_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_user_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_user_sync_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_user_sync_created: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_user_sync_updated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_user_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_user_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_account_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
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
