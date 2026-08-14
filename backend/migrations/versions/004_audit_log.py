"""Add audit columns to usage_records.

Revision ID: 004
Revises: 003
Create Date: 2026-05-31
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_records", sa.Column("operation_type", sa.String(50), nullable=True)
    )
    op.add_column("usage_records", sa.Column("query_text", sa.Text, nullable=True))
    op.add_column(
        "usage_records", sa.Column("rag_chunks_retrieved", sa.Integer, nullable=True)
    )
    op.add_column("usage_records", sa.Column("rag_source_ids", sa.JSON, nullable=True))
    op.add_column(
        "usage_records", sa.Column("ip_address", sa.String(45), nullable=True)
    )
    op.add_column(
        "usage_records", sa.Column("user_agent", sa.String(500), nullable=True)
    )

    op.create_index(
        "ix_usage_records_user_created",
        "usage_records",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_usage_records_operation_type",
        "usage_records",
        ["operation_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_operation_type", table_name="usage_records")
    op.drop_index("ix_usage_records_user_created", table_name="usage_records")
    op.drop_column("usage_records", "user_agent")
    op.drop_column("usage_records", "ip_address")
    op.drop_column("usage_records", "rag_source_ids")
    op.drop_column("usage_records", "rag_chunks_retrieved")
    op.drop_column("usage_records", "query_text")
    op.drop_column("usage_records", "operation_type")
