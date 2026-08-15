import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    String,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    JSON,
    Numeric,
    Text,
    Float,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        UniqueConstraint(
            "entra_tenant_id",
            "entra_object_id",
            name="uq_users_entra_identity",
        ),
        Index("idx_users_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="user", server_default="user")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oauth_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    oauth_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Immutable Microsoft Entra identity used by the Office NAA exchange.
    # Kept separately from oauth_subject because the latter historically used
    # the pairwise `sub` claim and can vary across app registrations.
    entra_tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entra_object_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # License (Sprint 8) — whether this user consumes a license seat
    license_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    premium_ai_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # Billing (added in migration 026)
    default_billing_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    # PAYG monthly spend cap (added in migration 038) — None means no cap
    payg_monthly_budget: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    # Enhanced user model
    practice_areas: Mapped[list | None] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    expertise_level: Mapped[str] = mapped_column(
        String(50), default="mid", server_default="mid"
    )
    default_skill: Mapped[str | None] = mapped_column(String(100), nullable=True)
    privacy_mode: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # Verified, user-managed professional profile.  This is intentionally
    # separate from role (authorization) and from learned memory.
    professional_role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    office_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_jurisdictions: Mapped[list | None] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    memory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_memory_update: Mapped[datetime | None] = mapped_column(
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

    tenant = relationship("Tenant", lazy="selectin")
    user_memories = relationship(
        "UserMemory", back_populates="user", cascade="all, delete-orphan"
    )


class UserMemory(Base):
    __tablename__ = "user_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "memory_type", "key", name="uq_user_memory_key"),
        Index("idx_user_memory_user_id", "user_id"),
        Index("idx_user_memory_type", "memory_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
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
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str | dict | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
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

    user = relationship("User", back_populates="user_memories")
