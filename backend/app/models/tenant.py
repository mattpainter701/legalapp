import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    staff_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    billing_tier: Mapped[str] = mapped_column(
        String(50), default="payg", server_default="payg"
    )
    api_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    flat_seat_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # Onboarding (Sprint 8)
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    onboarding_step: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )  # 0=not started, 1=consent, 2=syncing, 3=review, 4=complete
    cloud_root_folder: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # {onedrive: {id, url}, google_drive: {id, url}}
    service_account_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True
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


class TenantSettings(Base):
    """Per-tenant configuration overrides for cache, user defaults, and feature flags."""

    __tablename__ = "tenant_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_settings_tenant_id"),
        Index("idx_tenant_settings_tenant_id", "tenant_id"),
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

    # Cache configuration overrides
    cache_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    cache_ttl_multiplier: Mapped[float] = mapped_column(
        default=1.0, server_default="1.0"
    )  # 0.5-2.0 to adjust all TTLs

    # User default overrides (applied to new users)
    default_expertise_level: Mapped[str] = mapped_column(
        String(50), default="mid", server_default="mid"
    )
    default_practice_areas: Mapped[list | None] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    default_privacy_mode: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Feature flags
    enable_auto_memory: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    enable_pii_detection: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    enable_skill_routing: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    enable_matter_context: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    # Rate limiting
    max_requests_per_minute: Mapped[int | None] = mapped_column(nullable=True)
    max_daily_tokens: Mapped[int | None] = mapped_column(nullable=True)

    # Custom configuration (JSON for extensibility)
    custom_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Customer LLM (Sprint 8) — allows firm to use their own Gemini/Copilot subscription
    use_customer_llm: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    customer_llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    customer_llm_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Operator-assigned default LLM provider + model (platform-level providers only)
    # "deepseek" | "opencode" | "openrouter" | "anthropic" | "azure" | "gemini"
    default_llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Notes/audit info
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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
