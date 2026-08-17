import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Integer,
    BigInteger,
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
    # api_key is nulled out after migration 058 — hash + prefix are used instead.
    api_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    api_key_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    api_key_prefix: Mapped[str | None] = mapped_column(String(8), nullable=True)
    flat_seat_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    stripe_subscription_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none", server_default="none"
    )
    mcp_entitlement_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="disabled", server_default="disabled"
    )
    mcp_billing_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="disabled", server_default="disabled"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Onboarding (Sprint 8)
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    onboarding_step: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )  # 0=not started, 1=consent, 2=syncing, 3=review, 4=complete
    # Authoritative cache generation for tenant-private retrieval. Corpus
    # mutations increment this in the same database transaction as chunk/status
    # changes, making old materialized RAG keys unreachable after commit.
    rag_corpus_revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
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
    enable_task_board: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # Deliberately OFF by default, unlike every flag above it. Chat actions let
    # the assistant put proposed work — including drafted client email — onto a
    # firm's board, so it is enabled per tenant after review rather than
    # inherited silently by every existing tenant on deploy.
    enable_chat_actions: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
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

    # Primary cloud provider for document storage.
    # "onedrive" | "sharepoint" | "google_drive" | None (auto)
    primary_cloud_provider: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # Operator-assigned LiteLLM gateway aliases. Provider field is retained for
    # backward compatibility and should be "litellm" when set.
    default_llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_llm_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    premium_llm_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    premium_llm_model: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Notes/audit info
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Firm branding (Task 1303) — used on branded PDF exports (e.g. trust
    # ledger statements). All nullable; fall back to Tenant.name /
    # Tenant.address when unset.
    firm_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    firm_logo_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    firm_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    firm_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    firm_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    firm_website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    firm_pdf_footer: Mapped[str | None] = mapped_column(Text, nullable=True)

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
