"""Public OAuth client registrations for the workspace MCP authorization server."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Index, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkspaceMCPClient(Base):
    """One public desktop-client registration.

    Desktop MCP clients cannot keep a client secret. Redirect URIs are captured
    at registration and every authorization/code exchange requires an exact
    match. A registration grants no tenant access by itself.
    """

    __tablename__ = "workspace_mcp_clients"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_workspace_mcp_clients_status",
        ),
        Index("ix_workspace_mcp_clients_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    client_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    redirect_uris: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    grant_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    response_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none", server_default="none"
    )
    software_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    software_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
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

    @property
    def redirect_uri_set(self) -> frozenset[str]:
        return frozenset(
            value for value in self.redirect_uris if isinstance(value, str)
        )

    def is_active(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        return (
            self.status == "active"
            and self.revoked_at is None
            and self.expires_at > moment
        )
