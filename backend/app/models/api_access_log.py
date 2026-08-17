"""API access log for platform diagnostics — no payloads, no PII."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiAccessLog(Base):
    __tablename__ = "api_access_logs"
    __table_args__ = (
        Index("ix_api_access_logs_tenant_id", "tenant_id"),
        Index("ix_api_access_logs_created_at", "created_at"),
        Index("ix_api_access_logs_endpoint", "endpoint"),
        Index("ix_api_access_logs_user_id", "user_id"),
        Index("ix_api_access_logs_status_code", "status_code"),
        Index("ix_api_access_logs_request_id", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent_short: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Correlates this row with the error_logs row and the client-side response
    # for the same request. Nullable: rows written before this column existed
    # have none, and it is never required for the access log to be valid.
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
