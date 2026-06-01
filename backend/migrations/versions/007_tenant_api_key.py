"""Add api_key column to tenants for MCP authentication.

Revision ID: 007
Revises: 006
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("api_key", sa.String(64), nullable=True, unique=True),
    )
    op.create_index("ix_tenants_api_key", "tenants", ["api_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tenants_api_key", table_name="tenants")
    op.drop_column("tenants", "api_key")
