"""030 — User sync state on tenant_credentials

Revision ID: 030
Revises: 029
Create Date: 2026-06-03

Adds last-sync bookkeeping columns to tenant_credentials so the Integrations
panel can show how many directory users were pulled and when the last sync ran.
"""

from alembic import op
import sqlalchemy as sa

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_total", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_created", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_updated", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "tenant_credentials",
        sa.Column("last_user_sync_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_credentials", "last_user_sync_error")
    op.drop_column("tenant_credentials", "last_user_sync_status")
    op.drop_column("tenant_credentials", "last_user_sync_updated")
    op.drop_column("tenant_credentials", "last_user_sync_created")
    op.drop_column("tenant_credentials", "last_user_sync_total")
    op.drop_column("tenant_credentials", "last_user_sync_at")
