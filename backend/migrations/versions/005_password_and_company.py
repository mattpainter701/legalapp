"""Add password_hash to users, company fields to tenants.

Revision ID: 005
Revises: 004
Create Date: 2026-05-31 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))

    op.add_column("tenants", sa.Column("company_name", sa.String(255), nullable=True))
    op.add_column("tenants", sa.Column("staff_size", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("address", sa.String(500), nullable=True))
    op.add_column("tenants", sa.Column("phone", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "phone")
    op.drop_column("tenants", "address")
    op.drop_column("tenants", "staff_size")
    op.drop_column("tenants", "company_name")

    op.drop_column("users", "password_hash")
