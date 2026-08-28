import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ESignWebhookEvent(Base):
    __tablename__ = "esign_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "provider", "event_id", name="uq_esign_webhook_provider_event"
        ),
        Index("idx_esign_webhook_envelope", "provider", "envelope_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    envelope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
