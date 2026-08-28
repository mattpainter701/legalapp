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
LEGACY_UNKNOWN_MICROS = 1_000_000_000_000


def upgrade() -> None:
    # Existing rows predate value metering, so their cost is genuinely unknown.
    # Treating them as zero could admit a second full provider window on deploy.
    # A deliberately oversized hold fails closed until those rows leave every
    # active window or an operator backfills authoritative provider spend.
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
        sa.Column("pricing_model", sa.String(200), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Migration 134 FORCEs RLS on this shared ledger. Use the same narrowly
    # scoped transaction-local selector as the quota service so the cutover
    # actually reaches every tenant's pre-existing rows.
    op.execute("SELECT set_config('app.background_ai_quota_scope', 'on', true)")
    op.execute(
        sa.text(
            f"""
            UPDATE {TABLE}
            SET estimated_micros = :legacy_hold,
                actual_micros = CASE
                    WHEN status = 'settled' THEN :legacy_hold
                    ELSE actual_micros
                END,
                price_card_version = 'legacy-cutover-unknown',
                error_code = COALESCE(error_code, 'value_cutover_unknown')
            WHERE status IN ('reserved', 'settled', 'unknown')
            """
        ).bindparams(legacy_hold=LEGACY_UNKNOWN_MICROS)
    )
    op.execute("SELECT set_config('app.background_ai_quota_scope', 'off', true)")
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
        postgresql_where=sa.text(
            "status IN ('reserved', 'unknown') "
            "AND (status = 'reserved' OR reconciled_at IS NULL)"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_background_ai_usage_unreconciled", table_name=TABLE)
    op.drop_constraint(
        "ck_background_ai_usage_micros_nonnegative", TABLE, type_="check"
    )
    op.drop_column(TABLE, "reconcile_attempts")
    op.drop_column(TABLE, "reconciled_at")
    op.drop_column(TABLE, "pricing_model")
    op.drop_column(TABLE, "price_card_version")
    op.drop_column(TABLE, "actual_micros")
    op.drop_column(TABLE, "estimated_micros")
