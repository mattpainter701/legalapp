"""032 — Estate soft-delete column + beneficiary share constraint.

Revision ID: 032
Revises: 031
Create Date: 2026-06-04

Adds:
  - ``is_deleted`` boolean on ``estates`` for soft-delete (legal audit trail).
  - Check constraint on ``estate_beneficiaries.share_percentage`` (0–100).
"""

import sqlalchemy as sa
from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "estates",
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("idx_estates_is_deleted", "estates", ["is_deleted"])

    # Guard: only add constraint if the table exists (migration 030 may have
    # been partially applied in some environments).
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_name = 'estate_beneficiaries')"
        )
    )
    if result.scalar():
        op.create_check_constraint(
            "ck_beneficiary_share_pct",
            "estate_beneficiaries",
            "share_percentage >= 0 AND share_percentage <= 100",
        )


def downgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_name = 'estate_beneficiaries')"
        )
    )
    if result.scalar():
        op.drop_constraint(
            "ck_beneficiary_share_pct", "estate_beneficiaries", type_="check"
        )
    op.drop_index("idx_estates_is_deleted", "estates")
    op.drop_column("estates", "is_deleted")
