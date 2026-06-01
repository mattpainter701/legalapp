"""Add context tracking and PII flags to messages table.

Revision ID: 012
Revises: 011
Create Date: 2026-06-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "skill_applied",
            sa.String(100),
            nullable=True,
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "context_used",
            sa.JSON(),
            nullable=True,
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "context_relevance_scores",
            sa.JSON(),
            nullable=True,
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "pii_flags",
            sa.JSON(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "pii_flags")
    op.drop_column("messages", "context_relevance_scores")
    op.drop_column("messages", "context_used")
    op.drop_column("messages", "skill_applied")
