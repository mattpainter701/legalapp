"""086 - retain source files for document templates.

Revision ID: 086_pdf_template_sources
Revises: 085_durable_jobs
"""

from alembic import op
import sqlalchemy as sa

revision = "086_pdf_template_sources"
down_revision = "085_durable_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_templates", sa.Column("source_storage_path", sa.String(1000))
    )
    op.add_column("document_templates", sa.Column("source_filename", sa.String(500)))
    op.add_column(
        "document_templates", sa.Column("source_content_type", sa.String(100))
    )
    op.add_column("document_templates", sa.Column("source_sha256", sa.String(64)))
    op.add_column("document_templates", sa.Column("source_file_size", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("document_templates", "source_file_size")
    op.drop_column("document_templates", "source_sha256")
    op.drop_column("document_templates", "source_content_type")
    op.drop_column("document_templates", "source_filename")
    op.drop_column("document_templates", "source_storage_path")
