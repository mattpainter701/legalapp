"""Retain immutable original evidence for derived Word template drafts.

Revision ID: 164_word_derived_source_evidence
Revises: 162_storage_migrations
"""

from alembic import op
import sqlalchemy as sa


revision = "164_word_derived_source_evidence"
down_revision = "162_storage_migrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_templates",
        sa.Column(
            "source_evidence_storage_path", sa.String(length=1000), nullable=True
        ),
    )
    op.add_column(
        "document_templates",
        sa.Column("source_evidence_filename", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("source_evidence_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "document_templates", sa.Column("source_provenance", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("document_templates", "source_provenance")
    op.drop_column("document_templates", "source_evidence_sha256")
    op.drop_column("document_templates", "source_evidence_filename")
    op.drop_column("document_templates", "source_evidence_storage_path")
