import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketingDemoRequest(Base):
    """Public marketing inquiry retained independently of tenant CRM data."""

    __tablename__ = "marketing_demo_requests"
    __table_args__ = (
        Index("idx_marketing_demo_requests_created_at", "created_at"),
        Index("idx_marketing_demo_requests_email", "email"),
        Index("idx_marketing_demo_requests_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    firm_name: Mapped[str] = mapped_column(String(300), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(60), nullable=True)
    team_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_path: Mapped[str] = mapped_column(
        String(500), nullable=False, default="/demo"
    )
    campaign: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="new", server_default="new"
    )
    notification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class MarketingEvent(Base):
    """Minimal first-party conversion event; contains no free-form visitor content."""

    __tablename__ = "marketing_events"
    __table_args__ = (
        Index("idx_marketing_events_name_created", "name", "created_at"),
        Index("idx_marketing_events_session", "session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    page: Mapped[str] = mapped_column(String(500), nullable=False)
    properties: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
