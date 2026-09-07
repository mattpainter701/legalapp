"""Persist generated-PDF-bound positioned signing fields."""

from alembic import op
import sqlalchemy as sa

revision = "163_signature_placements"
down_revision = "162_storage_migrations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "signature_requests",
        sa.Column("positioned_fields", sa.JSON(), nullable=True),
    )
    op.add_column(
        "matter_documents",
        sa.Column("positioned_fields", sa.JSON(), nullable=True),
    )
    op.add_column("matter_documents", sa.Column("signing_placement_required", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    op.drop_column("matter_documents", "signing_placement_required")
    op.drop_column("signature_requests", "positioned_fields")
    op.drop_column("matter_documents", "positioned_fields")
