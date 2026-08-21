"""Durable end-user consent grants for the LawHand workspace MCP resource."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkspaceMCPGrant(Base):
    """One revocable user/client/scope consent grant.

    Access tokens are only short-lived assertions about this row. The resource
    server re-reads the grant so revocation, expiry, or a scope reduction takes
    effect even while a previously issued JWT remains cryptographically valid.
    """

    __tablename__ = "workspace_mcp_grants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_workspace_mcp_grants_status",
        ),
        Index(
            "idx_workspace_mcp_grants_tenant_user_status",
            "tenant_id",
            "user_id",
            "status",
        ),
        Index(
            "idx_workspace_mcp_grants_tenant_client_status",
            "tenant_id",
            "client_id",
            "status",
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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id: Mapped[str] = mapped_column(String(200), nullable=False)
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    consent_version: Mapped[str] = mapped_column(String(50), nullable=False)
    consent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def is_active(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        return (
            self.status == "active"
            and self.revoked_at is None
            and self.expires_at > moment
        )

    @property
    def scope_set(self) -> frozenset[str]:
        if not isinstance(self.scopes, list):
            return frozenset()
        return frozenset(
            value.strip()
            for value in self.scopes
            if isinstance(value, str) and value.strip()
        )
