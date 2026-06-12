"""051 — Matter document storage backend metadata.

Records whether an uploaded matter document landed in OneDrive, Google Drive,
or local fallback storage, plus the cloud failure reason when fallback is used.
"""

from alembic import op
import sqlalchemy as sa

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matter_documents",
        sa.Column("storage_backend", sa.String(50), nullable=True),
    )
    op.add_column(
        "matter_documents",
        sa.Column("storage_error", sa.String(1000), nullable=True),
    )
    op.execute("""
        UPDATE matter_documents
        SET storage_backend = CASE
            WHEN storage_path ILIKE 'https://drive.google.com/%' THEN 'google_drive'
            WHEN storage_path ILIKE 'https://%' THEN 'onedrive'
            WHEN storage_path IS NOT NULL THEN 'local'
            ELSE NULL
        END
        WHERE storage_backend IS NULL
        """)
    op.create_index(
        "ix_matter_documents_storage_backend",
        "matter_documents",
        ["storage_backend"],
    )


def downgrade() -> None:
    op.drop_index("ix_matter_documents_storage_backend", table_name="matter_documents")
    op.drop_column("matter_documents", "storage_error")
    op.drop_column("matter_documents", "storage_backend")
