"""083 - matter document storage metadata

Revision ID: 083_matter_document_storage_metadata
Revises: 082_integration_observability
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa

revision = "083_matter_document_storage_metadata"
down_revision = "082_integration_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matter_documents",
        sa.Column("storage_provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "matter_documents",
        sa.Column("storage_backend", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "matter_documents",
        sa.Column("provider_object_id", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "matter_documents",
        sa.Column("provider_drive_id", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "matter_documents",
        sa.Column("provider_parent_id", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "matter_documents",
        sa.Column("storage_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("matter_documents", "storage_error")
    op.drop_column("matter_documents", "provider_parent_id")
    op.drop_column("matter_documents", "provider_drive_id")
    op.drop_column("matter_documents", "provider_object_id")
    op.drop_column("matter_documents", "storage_backend")
    op.drop_column("matter_documents", "storage_provider")
