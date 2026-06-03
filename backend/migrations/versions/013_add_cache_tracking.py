"""Add cache tracking fields to usage_records table.

Revision ID: 013
Revises: 012
Create Date: 2026-06-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "usage_records",
        sa.Column(
            "cache_hit_rag",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "usage_records",
        sa.Column(
            "cache_hit_llm",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "usage_records",
        sa.Column(
            "cache_hit_matter",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("usage_records", "cache_hit_matter")
    op.drop_column("usage_records", "cache_hit_llm")
    op.drop_column("usage_records", "cache_hit_rag")
