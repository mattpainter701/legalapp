"""Error logging model for troubleshooting and support."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, ForeignKey, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ErrorLog(Base):
    """Global error log for troubleshooting and support management."""

    __tablename__ = "error_logs"
    __table_args__ = (
        Index("idx_error_logs_user_id", "user_id"),
        Index("idx_error_logs_tenant_id", "tenant_id"),
        Index("idx_error_logs_created_at", "created_at"),
        Index("idx_error_logs_severity", "severity"),
        Index("idx_error_logs_error_type", "error_type"),
        # Composite index for per-user recent errors
        Index("idx_error_logs_user_recent", "user_id", "created_at"),
        # Composite index for system errors
        Index("idx_error_logs_system_recent", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,  # NULL for system-level errors
    )

    # Error classification
    error_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # error_type examples: "api_error", "rag_query_error", "llm_error", "cache_error",
    #                      "database_error", "authentication_error", "validation_error",
    #                      "timeout_error", "rate_limit_error", "permission_error"

    severity: Mapped[str] = mapped_column(
        String(20), default="error", server_default="error"
    )
    # Severity levels: "critical", "error", "warning", "info"

    # Error details
    message: Mapped[str] = mapped_column(Text, nullable=False)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Request context for debugging
    endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status_code: Mapped[int | None] = mapped_column(nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Error context
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Resolution tracking
    is_resolved: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
