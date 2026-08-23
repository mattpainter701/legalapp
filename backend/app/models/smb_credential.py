"""SMB credential model — per-tenant secure storage for file share auth.

The secret itself is never stored in plaintext: ``encrypted_password`` holds a
Fernet ciphertext produced by ``app.services.token_vault`` (the same keyring
used for OAuth credentials), so rotating ``TOKEN_ENCRYPTION_KEYS`` rotates
share credentials too. Rows are tenant-scoped and RLS-protected; the plaintext
only leaves the backend over the agent-authenticated credential endpoint.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Auth methods an agent knows how to use against an SMB server.
AUTH_METHODS = ("ntlm", "kerberos", "guest")


class SmbCredential(Base):
    __tablename__ = "smb_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_smb_credentials_tenant_name"),
        Index("ix_smb_credentials_tenant_id", "tenant_id"),
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
    # Admin-facing label, e.g. "svc-lawhand (CORP)".
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    auth_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ntlm", server_default="ntlm"
    )
    domain: Mapped[str | None] = mapped_column(String(200), nullable=True)
    username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Fernet ciphertext. Null for kerberos (ticket cache) and guest auth.
    encrypted_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional scoping: a credential may be pinned to one agent so a secret for
    # one office's file server is never handed to another office's agent.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("smb_agents.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_verify_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_verify_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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
