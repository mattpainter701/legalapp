"""Tenant-scoped, tamper-evident workspace MCP audit events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkspaceMCPAuditEvent(Base):
    """Metadata-only evidence for consent, token, and tool activity."""

    __tablename__ = "workspace_mcp_audit_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "chain_position", name="uq_workspace_mcp_audit_tenant_position"
        ),
        UniqueConstraint(
            "tenant_id", "event_hash", name="uq_workspace_mcp_audit_tenant_hash"
        ),
        CheckConstraint(
            "outcome IN ('success', 'denied', 'error')",
            name="ck_workspace_mcp_audit_outcome",
        ),
        CheckConstraint(
            "chain_position > 0", name="ck_workspace_mcp_audit_chain_position"
        ),
        CheckConstraint(
            "prev_event_hash IS NULL OR prev_event_hash ~ '^[0-9a-f]{64}$'",
            name="ck_workspace_mcp_audit_prev_hash",
        ),
        CheckConstraint(
            "event_hash ~ '^[0-9a-f]{64}$'", name="ck_workspace_mcp_audit_event_hash"
        ),
        Index("ix_workspace_mcp_audit_tenant_created", "tenant_id", "created_at", "id"),
        Index(
            "ix_workspace_mcp_audit_tenant_grant_created",
            "tenant_id",
            "grant_id",
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
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    grant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_mcp_grants.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, default=dict, server_default="{}"
    )
    chain_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prev_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
