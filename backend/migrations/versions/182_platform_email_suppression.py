"""Platform-wide outbound email suppression list and provider webhook receipts.

Revision ID: 182_platform_email_suppression
Revises: 181_session_epoch

Both tables are deliberately platform-scoped and carry no RLS policy, matching
the precedent set by ``stripe_webhook_events``: a bounce is keyed on the
recipient mailbox and arrives before any tenant can be resolved.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "182_platform_email_suppression"
down_revision = "181_session_epoch"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_suppressions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("provider_payload", JSONB(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("email", name="uq_email_suppressions_email"),
        sa.CheckConstraint(
            "reason IN ('hard_bounce', 'spam_complaint', 'manual', 'bad_mailbox')",
            name="ck_email_suppression_reason",
        ),
    )
    op.create_index(
        "idx_email_suppressions_created", "email_suppressions", ["created_at"]
    )

    op.create_table(
        "platform_email_webhook_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("record_type", sa.String(60), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider", "event_id", name="uq_platform_email_webhook_provider_event"
        ),
    )
    op.create_index(
        "idx_platform_email_webhook_received",
        "platform_email_webhook_events",
        ["received_at"],
    )


def downgrade():
    op.drop_index(
        "idx_platform_email_webhook_received",
        table_name="platform_email_webhook_events",
    )
    op.drop_table("platform_email_webhook_events")
    op.drop_index("idx_email_suppressions_created", table_name="email_suppressions")
    op.drop_table("email_suppressions")
