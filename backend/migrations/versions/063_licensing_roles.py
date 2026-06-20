"""Licensing roles and premium AI assignment.

Revision ID: 063
Revises: 062
Create Date: 2026-06-20
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "063"
down_revision: Union[str, None] = "062"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "premium_ai_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "premium_ai_enabled")
