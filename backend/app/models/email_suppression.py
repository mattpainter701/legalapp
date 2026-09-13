"""Platform-wide outbound email suppression and provider webhook receipts.

Deliberately not tenant-scoped, for the same reason as
``stripe_webhook_events``: a bounce arrives from the mail provider keyed only
on the recipient address, before (and sometimes without) any tenant being
resolvable. The same address may also belong to users in several tenants, and
a hard bounce is a property of the mailbox rather than of any one firm. These
tables therefore carry no RLS policy and must only ever be reached by platform
code paths, never by a tenant-scoped query.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Delivery failures that mean the mailbox will never accept mail again. A
# transient bounce (full mailbox, greylisting, temporary DNS failure) must not
# land here: suppressing on those would lock a user out of their own account
# over a momentary outage at their mail host.
PERMANENT_SUPPRESSION_REASONS = frozenset(
    {"hard_bounce", "spam_complaint", "manual", "bad_mailbox"}
)


class EmailSuppression(Base):
    """An address the platform must stop sending system email to.

    Repeatedly mailing a dead address is what turns a sending domain's
    reputation bad, so a hard bounce or spam complaint is recorded once and
    enforced before every send.
    """

    __tablename__ = "email_suppressions"
    __table_args__ = (
        UniqueConstraint("email", name="uq_email_suppressions_email"),
        CheckConstraint(
            "reason IN ('hard_bounce', 'spam_complaint', 'manual', 'bad_mailbox')",
            name="ck_email_suppression_reason",
        ),
        Index("idx_email_suppressions_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Stored normalized (lower-cased, trimmed). The address is already held in
    # plaintext on ``users.email``, so hashing here would buy no confidentiality
    # while making the list unreadable to an operator answering "why did this
    # customer stop receiving reset links?".
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    # Provider-specific detail (bounce type, description, provider message id)
    # kept for support triage. Never contains message content.
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class PlatformEmailWebhookEvent(Base):
    """One row per provider webhook delivery already applied.

    Transactional providers retry until they see a 2xx, so the handler needs an
    idempotency key to keep a redelivery from re-suppressing an address an
    operator has since released.
    """

    __tablename__ = "platform_email_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "provider", "event_id", name="uq_platform_email_webhook_provider_event"
        ),
        Index("idx_platform_email_webhook_received", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    record_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
