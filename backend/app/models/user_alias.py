import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserAliasAddress(Base):
    """A tenant-scoped secondary address for a user.

    Verification is deliberately separate from ownership/provenance.  An
    administrator may add a pending address, but only the one-time token
    issued to that address can make it usable for authentication.
    """

    __tablename__ = "user_alias_addresses"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "normalized_address", name="uq_user_alias_tenant_address"
        ),
        Index("idx_user_alias_user", "tenant_id", "user_id"),
        Index(
            "idx_user_alias_verified", "tenant_id", "normalized_address", "is_verified"
        ),
        ForeignKeyConstraint(
            ("tenant_id", "user_id"),
            ("users.tenant_id", "users.id"),
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_address: Mapped[str] = mapped_column(String(255), nullable=False)
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    verification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )

    user = relationship("User", back_populates="alias_addresses")
