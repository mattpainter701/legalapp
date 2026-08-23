import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StripeWebhookEvent(Base):
    """One row per Stripe event the application has applied.

    Stripe retries until it sees a 2xx and makes no ordering guarantee, so a
    handler needs two things this table provides: an idempotency key, and a
    per-object high-water mark to compare an arriving event against.

    Deliberately not tenant-scoped. The row is written before the tenant is
    resolved (and events for unknown customers must still be recorded), so it
    carries no RLS policy and is keyed only on Stripe's own identifiers.
    """

    __tablename__ = "stripe_webhook_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_stripe_webhook_events_event_id"),
        Index("idx_stripe_webhook_events_object_created", "object_id", "event_created"),
        Index("idx_stripe_webhook_events_processed_at", "processed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_created: Mapped[int] = mapped_column(BigInteger, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
