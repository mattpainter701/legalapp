"""Add task read-receipt and customer-contact tracking columns.

Revision ID: 073
Revises: 072
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "073_task_read_receipts"
down_revision: Union[str, None] = "072_cred_unique_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks", sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "tasks",
        sa.Column("customer_contacted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks", sa.Column("customer_contact_method", sa.String(50), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tasks", "customer_contact_method")
    op.drop_column("tasks", "customer_contacted_at")
    op.drop_column("tasks", "viewed_at")
