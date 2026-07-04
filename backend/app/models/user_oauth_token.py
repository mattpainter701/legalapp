import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text, UniqueConstraint, Integer
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class UserOAuthToken(Base):
    __tablename__ = "user_oauth_tokens"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "provider",
            name="ix_user_oauth_tokens_tenant_user_provider",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
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
