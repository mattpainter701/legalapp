"""Platform quota reservations for the global Background Automations route."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BackgroundAIUsageReservation(Base):
    """One request-cap reservation against the shared provider subscription pool.

    This table intentionally stores operational metadata only. Prompt and response
    content never belongs in the platform-wide ledger.
    """

    __tablename__ = "background_ai_usage_reservations"
    __table_args__ = (
        UniqueConstraint(
            "pool",
            "idempotency_key",
            name="uq_background_ai_usage_pool_idempotency",
        ),
        CheckConstraint(
            "status IN ('reserved', 'settled', 'unknown', 'released')",
            name="ck_background_ai_usage_status",
        ),
        CheckConstraint(
            "tokens_in >= 0 AND tokens_out >= 0",
            name="ck_background_ai_usage_tokens_nonnegative",
        ),
        CheckConstraint(
            "estimated_micros >= 0 AND actual_micros >= 0",
            name="ck_background_ai_usage_micros_nonnegative",
        ),
        Index(
            # Drives the reconciliation sweep for ambiguous reservations.
            "ix_background_ai_usage_unreconciled",
            "status",
            "created_at",
            postgresql_where=text(
                "status IN ('reserved', 'unknown') "
                "AND (status = 'reserved' OR reconciled_at IS NULL)"
            ),
        ),
        Index(
            "ix_background_ai_usage_pool_created",
            "pool",
            "created_at",
        ),
        Index(
            "ix_background_ai_usage_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_background_ai_usage_surface_created",
            "surface",
            "created_at",
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
    pool: Mapped[str] = mapped_column(
        String(80), nullable=False, default="background-default"
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    surface: Mapped[str] = mapped_column(String(80), nullable=False)
    route_alias: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="reserved", server_default="reserved"
    )
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tokens_in: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    tokens_out: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Provider value in USD micros (1 USD == 1_000_000). The pool's real limits
    # are dollar windows, so admission reasons in money and treats request
    # counts as a secondary backstop.
    #
    # ``estimated_micros`` is the worst case priced at reservation time and is
    # what an in-flight or ambiguous reservation holds against the budget.
    # ``actual_micros`` replaces it once the provider reports real usage.
    estimated_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    actual_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    price_card_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Provider-qualified model that produced the largest reservation estimate.
    # The full active graph is versioned by ``route_alias``; this field makes the
    # conservative pricing decision independently auditable.
    pricing_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Set when reconciliation has resolved this row or given up on it, so the
    # sweep never reprocesses the same ambiguous reservation forever.
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconcile_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
