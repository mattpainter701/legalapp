"""Add the platform Background Automations request reservation ledger.

Revision ID: 132_background_ai_quota
Revises: 131_prospect_follow_through
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "132_background_ai_quota"
down_revision = "131_prospect_follow_through"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is a platform admission-control ledger, not a tenant content table.
    # It contains IDs and counters only and must be readable across tenants so a
    # single shared subscription cap can be enforced atomically.
    op.create_table(
        "background_ai_usage_reservations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pool",
            sa.String(80),
            nullable=False,
            server_default="background-default",
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("surface", sa.String(80), nullable=False),
        sa.Column("route_alias", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="reserved"),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "pool",
            "idempotency_key",
            name="uq_background_ai_usage_pool_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'unknown', 'released')",
            name="ck_background_ai_usage_status",
        ),
        sa.CheckConstraint(
            "tokens_in >= 0 AND tokens_out >= 0",
            name="ck_background_ai_usage_tokens_nonnegative",
        ),
    )
    op.create_index(
        "ix_background_ai_usage_pool_created",
        "background_ai_usage_reservations",
        ["pool", "created_at"],
    )
    op.create_index(
        "ix_background_ai_usage_tenant_created",
        "background_ai_usage_reservations",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_background_ai_usage_surface_created",
        "background_ai_usage_reservations",
        ["surface", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("background_ai_usage_reservations")
