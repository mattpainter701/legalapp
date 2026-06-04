"""034 — Matter cloud folder metadata.

Revision ID: 034
Revises: 033
Create Date: 2026-06-04

Persists provider folder IDs/URLs created for each matter so uploads and UI can
anchor matter documents in the customer's connected cloud storage.
"""

from alembic import op
import sqlalchemy as sa

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("matters", sa.Column("cloud_folder", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("matters", "cloud_folder")
