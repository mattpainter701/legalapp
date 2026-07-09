"""084 - bind signature acknowledgments to source and evidence digests.

Revision ID: 084_esign_evidence_integrity
Revises: 083_matter_doc_storage_meta
"""

from alembic import op
import sqlalchemy as sa

revision = "084_esign_evidence_integrity"
down_revision = "083_matter_doc_storage_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signature_requests", sa.Column("source_document_sha256", sa.String(64))
    )
    op.add_column("signature_requests", sa.Column("source_document_size", sa.Integer()))
    op.add_column(
        "signature_requests", sa.Column("source_document_filename", sa.String(500))
    )
    op.add_column(
        "signature_requests", sa.Column("completion_artifact_sha256", sa.String(64))
    )
    op.add_column("signature_requests", sa.Column("evidence_sha256", sa.String(64)))


def downgrade() -> None:
    op.drop_column("signature_requests", "evidence_sha256")
    op.drop_column("signature_requests", "completion_artifact_sha256")
    op.drop_column("signature_requests", "source_document_filename")
    op.drop_column("signature_requests", "source_document_size")
    op.drop_column("signature_requests", "source_document_sha256")
