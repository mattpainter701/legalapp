"""023 — Add reminder_sent_at to tasks table.

Revision ID: 023
Revises: 022
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "reminder_sent_at")
