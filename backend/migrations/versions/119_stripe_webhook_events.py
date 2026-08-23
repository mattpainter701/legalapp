"""Record processed Stripe events for idempotency and ordering.

Stripe retries a webhook until it receives a 2xx and does not guarantee
delivery order. This table gives both handlers a place to (a) reject a
replayed event id and (b) refuse to apply an event older than the one already
applied for the same Stripe object.

Revision ID: 119_stripe_webhook_events
Revises: 118_workspace_mcp_oauth
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "119_stripe_webhook_events"
down_revision = "118_workspace_mcp_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stripe_webhook_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Stripe's own event id (evt_...). Unique: this is the idempotency key.
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        # The Stripe object the event concerns (sub_..., cus_..., in_...), used
        # to order events against each other. Null when an event carries no
        # object we can key ordering on.
        sa.Column("object_id", sa.String(255), nullable=True),
        # Stripe's event.created, in epoch seconds. Ordering is decided on this
        # rather than arrival time, which is what makes out-of-order retries safe.
        sa.Column("event_created", sa.BigInteger(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("event_id", name="uq_stripe_webhook_events_event_id"),
    )
    op.create_index(
        "idx_stripe_webhook_events_object_created",
        "stripe_webhook_events",
        ["object_id", "event_created"],
    )
    op.create_index(
        "idx_stripe_webhook_events_processed_at",
        "stripe_webhook_events",
        ["processed_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_stripe_webhook_events_processed_at", "stripe_webhook_events")
    op.drop_index("idx_stripe_webhook_events_object_created", "stripe_webhook_events")
    op.drop_table("stripe_webhook_events")
