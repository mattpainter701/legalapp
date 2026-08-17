"""Operator API keys minted by the platform console.

Unlike tenant data these rows are deliberately *not* under RLS: they describe
the SaaS operator's own credentials, not any tenant's records. Only the SHA-256
hash of a key is stored — the plaintext is returned exactly once, at mint time,
and is unrecoverable afterwards.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlatformApiKey(Base):
    __tablename__ = "platform_api_keys"
    __table_args__ = (
        Index("idx_platform_api_keys_key_hash", "key_hash", unique=True),
        Index("idx_platform_api_keys_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )

    # Operator-facing name, e.g. "matt-laptop" or "pagerduty-runbook".
    label: Mapped[str] = mapped_column(String(120), nullable=False)

    # First characters of the plaintext, retained so the console can show which
    # key a row refers to without being able to reconstruct it.
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Written at most once a minute per key so an operator can spot a key that
    # is still live in some forgotten script before revoking it.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def is_usable(self, now: datetime | None = None) -> bool:
        """Fail closed: a key is usable only while unrevoked and unexpired."""
        moment = now or datetime.now(timezone.utc)
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= moment:
            return False
        return True
