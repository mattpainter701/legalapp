"""041 — Create error_logs table for platform diagnostics.

Revision ID: 041
Revises: 040
Create Date: 2026-06-05

Adds error_logs table — structured error logging for troubleshooting.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "error_logs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("error_type", sa.String(100), nullable=False),
        sa.Column(
            "severity",
            sa.String(20),
            nullable=False,
            server_default="error",
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("stack_trace", sa.Text(), nullable=True),
        sa.Column("endpoint", sa.String(255), nullable=True),
        sa.Column("method", sa.String(10), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column(
            "is_resolved",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    op.create_index("idx_error_logs_user_id", "error_logs", ["user_id"])
    op.create_index("idx_error_logs_tenant_id", "error_logs", ["tenant_id"])
    op.create_index("idx_error_logs_created_at", "error_logs", ["created_at"])
    op.create_index("idx_error_logs_severity", "error_logs", ["severity"])
    op.create_index("idx_error_logs_error_type", "error_logs", ["error_type"])
    op.create_index(
        "idx_error_logs_user_recent",
        "error_logs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "idx_error_logs_system_recent",
        "error_logs",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_error_logs_system_recent", table_name="error_logs")
    op.drop_index("idx_error_logs_user_recent", table_name="error_logs")
    op.drop_index("idx_error_logs_error_type", table_name="error_logs")
    op.drop_index("idx_error_logs_severity", table_name="error_logs")
    op.drop_index("idx_error_logs_created_at", table_name="error_logs")
    op.drop_index("idx_error_logs_tenant_id", table_name="error_logs")
    op.drop_index("idx_error_logs_user_id", table_name="error_logs")
    op.drop_table("error_logs")
