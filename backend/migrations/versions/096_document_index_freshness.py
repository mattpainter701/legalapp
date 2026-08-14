"""Add document indexing freshness and embedding provenance.

Revision ID: 096_document_index_freshness
Revises: 095_office_assistant
"""

from alembic import op
import sqlalchemy as sa


revision = "096_document_index_freshness"
down_revision = "095_office_assistant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "documents",
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("documents", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column(
        "documents", sa.Column("embedding_model", sa.String(255), nullable=True)
    )
    op.add_column("documents", sa.Column("embedding_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "embedding_version")
    op.drop_column("documents", "embedding_model")
    op.drop_column("documents", "content_hash")
    op.drop_column("documents", "source_modified_at")
    op.drop_column("documents", "indexed_at")
