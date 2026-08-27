"""Meter the Background Automations pool in provider value, not request counts.

The pool's real provider limits are dollar windows. Counting requests lets a
burst of long-context work exhaust the actual budget while the counter still
reports headroom, so each reservation now carries the spend it holds.

Revision ID: 137_background_ai_value_quota
Revises: 136_qbo_invoice_billing
"""

from alembic import op
import sqlalchemy as sa


revision = "137_background_ai_value_quota"
down_revision = "136_qbo_invoice_billing"
branch_labels = None
depends_on = None


TABLE = "background_ai_usage_reservations"


def upgrade() -> None:
    # Existing rows predate value metering. They keep 0 micros, which makes them
    # invisible to the value windows while their request counts still apply —
    # the correct read, because no price was ever recorded for them.
    op.add_column(
        TABLE,
        sa.Column(
            "estimated_micros",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        TABLE,
        sa.Column(
            "actual_micros",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        TABLE,
        sa.Column("price_card_version", sa.String(40), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column(
            "reconcile_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_background_ai_usage_micros_nonnegative",
        TABLE,
        "estimated_micros >= 0 AND actual_micros >= 0",
    )
    # Partial index: the reconciliation sweep only ever scans ambiguous rows.
    op.create_index(
        "ix_background_ai_usage_unreconciled",
        TABLE,
        ["status", "created_at"],
        postgresql_where=sa.text("status = 'unknown' AND reconciled_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_background_ai_usage_unreconciled", table_name=TABLE)
    op.drop_constraint(
        "ck_background_ai_usage_micros_nonnegative", TABLE, type_="check"
    )
    op.drop_column(TABLE, "reconcile_attempts")
    op.drop_column(TABLE, "reconciled_at")
    op.drop_column(TABLE, "price_card_version")
    op.drop_column(TABLE, "actual_micros")
    op.drop_column(TABLE, "estimated_micros")
