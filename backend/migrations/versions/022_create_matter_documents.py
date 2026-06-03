"""022 — Create matter_documents table.

Revision ID: 022
Revises: 021b
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "022"
down_revision = "021b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "matter_documents",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("storage_path", sa.String(1000), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("document_category", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("idx_matterdocs_tenant_id", "matter_documents", ["tenant_id"])
    op.create_index("idx_matterdocs_matter_id", "matter_documents", ["matter_id"])
    op.create_index(
        "idx_matterdocs_created_at", "matter_documents", ["tenant_id", "created_at"]
    )

    op.execute("ALTER TABLE matter_documents ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY matter_documents_tenant_isolation ON matter_documents
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS matter_documents_tenant_isolation ON matter_documents"
    )
    op.drop_table("matter_documents")
