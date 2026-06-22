"""Add Zoom Phone webhook secret storage

Revision ID: 067
Revises: 066
Create Date: 2026-06-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_oauth_apps",
        sa.Column("encrypted_webhook_secret_token", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_oauth_apps", "encrypted_webhook_secret_token")
