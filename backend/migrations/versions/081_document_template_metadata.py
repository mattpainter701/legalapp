"""Add document template metadata and lifecycle fields.

Revision ID: 081_document_template_metadata
Revises: 080
Create Date: 2026-07-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "081_document_template_metadata"
down_revision: Union[str, None] = "080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_templates",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column(
            "visibility", sa.String(length=50), server_default="tenant", nullable=True
        ),
    )
    op.add_column(
        "document_templates",
        sa.Column("layer", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column(
            "status", sa.String(length=50), server_default="draft", nullable=True
        ),
    )
    op.add_column(
        "document_templates",
        sa.Column(
            "format", sa.String(length=50), server_default="markdown", nullable=True
        ),
    )
    op.add_column(
        "document_templates",
        sa.Column("module", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("stage", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("jurisdiction", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("kind", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("variable_schema", sa.JSON(), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("signer_roles", sa.JSON(), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("branding_profile", sa.JSON(), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("last_test_rendered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("approved_by_user_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_document_templates_approved_by_user_id_users",
        "document_templates",
        "users",
        ["approved_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_doc_templates_tenant_status",
        "document_templates",
        ["tenant_id", "status"],
    )
    op.create_index(
        "idx_doc_templates_tenant_format",
        "document_templates",
        ["tenant_id", "format"],
    )


def downgrade() -> None:
    op.drop_index("idx_doc_templates_tenant_format", table_name="document_templates")
    op.drop_index("idx_doc_templates_tenant_status", table_name="document_templates")
    op.drop_constraint(
        "fk_document_templates_approved_by_user_id_users",
        "document_templates",
        type_="foreignkey",
    )
    op.drop_column("document_templates", "approved_by_user_id")
    op.drop_column("document_templates", "approved_at")
    op.drop_column("document_templates", "last_test_rendered_at")
    op.drop_column("document_templates", "branding_profile")
    op.drop_column("document_templates", "signer_roles")
    op.drop_column("document_templates", "variable_schema")
    op.drop_column("document_templates", "kind")
    op.drop_column("document_templates", "jurisdiction")
    op.drop_column("document_templates", "stage")
    op.drop_column("document_templates", "module")
    op.drop_column("document_templates", "format")
    op.drop_column("document_templates", "status")
    op.drop_column("document_templates", "layer")
    op.drop_column("document_templates", "visibility")
    op.drop_column("document_templates", "description")
