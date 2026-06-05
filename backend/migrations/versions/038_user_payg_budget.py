"""038 — Add payg_monthly_budget to users table.

Revision ID: 038
Revises: 037
Create Date: 2026-06-05

Adds a nullable Numeric(10,2) column to users for per-user PAYG spend caps.
"""

from alembic import op
import sqlalchemy as sa

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("payg_monthly_budget", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "payg_monthly_budget")
