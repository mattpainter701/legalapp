"""025 — Create document_templates table.

Revision ID: 025
Revises: 024
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_templates",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="other"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
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

    op.create_index("idx_doc_templates_tenant_id", "document_templates", ["tenant_id"])
    op.create_index(
        "idx_doc_templates_tenant_active",
        "document_templates",
        ["tenant_id", "is_active"],
    )

    op.execute("ALTER TABLE document_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_templates FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_document_templates ON document_templates
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_document_templates ON document_templates"
    )
    op.drop_table("document_templates")
