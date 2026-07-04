"""Add task closure-reason tracking columns.

Revision ID: 074
Revises: 073
Create Date: 2026-07-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "074_task_closure_tracking"
down_revision: Union[str, None] = "073_task_read_receipts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("closed_reason", sa.Text(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column(
            "closed_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "closed_by_user_id")
    op.drop_column("tasks", "closed_reason")
