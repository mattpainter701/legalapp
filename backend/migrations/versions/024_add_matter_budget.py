"""024 — Add budget fields to matters table.

Revision ID: 024
Revises: 023
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matters", sa.Column("budget_amount", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column(
        "matters",
        sa.Column(
            "budget_currency", sa.String(3), nullable=False, server_default="USD"
        ),
    )


def downgrade() -> None:
    op.drop_column("matters", "budget_currency")
    op.drop_column("matters", "budget_amount")
