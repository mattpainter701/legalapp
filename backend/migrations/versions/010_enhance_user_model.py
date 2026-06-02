"""Add user preferences and memory fields to users table.

Revision ID: 010
Revises: 009
Create Date: 2026-06-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "practice_areas",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "expertise_level",
            sa.String(50),
            nullable=False,
            server_default="mid",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "default_skill",
            sa.String(100),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "privacy_mode",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "memory_summary",
            sa.Text(),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "last_memory_update",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_users_tenant_id",
        "users",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_users_tenant_id", table_name="users")
    op.drop_column("users", "practice_areas")
    op.drop_column("users", "expertise_level")
    op.drop_column("users", "default_skill")
    op.drop_column("users", "privacy_mode")
    op.drop_column("users", "memory_summary")
    op.drop_column("users", "last_memory_update")
