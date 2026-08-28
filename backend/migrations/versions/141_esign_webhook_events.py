"""Idempotency records for authenticated external e-sign webhooks."""

from alembic import op
import sqlalchemy as sa

revision = "141_esign_webhook_events"
down_revision = "140_client_portal_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "esign_webhook_events",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("envelope_id", sa.String(255), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider", "event_id", name="uq_esign_webhook_provider_event"
        ),
    )
    op.create_index(
        "idx_esign_webhook_envelope",
        "esign_webhook_events",
        ["provider", "envelope_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_esign_webhook_envelope", table_name="esign_webhook_events")
    op.drop_table("esign_webhook_events")
