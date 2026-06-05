"""039 — API access log table for platform diagnostics.

Revision ID: 039
Revises: 038
Create Date: 2026-06-05

Adds api_access_logs table — lightweight per-request metadata (no payloads, no PII).
Used by the operator console to diagnose tenant API usage patterns.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_access_logs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent_short", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )

    op.create_index(
        "ix_api_access_logs_tenant_id",
        "api_access_logs",
        ["tenant_id"],
    )
    op.create_index(
        "ix_api_access_logs_created_at",
        "api_access_logs",
        ["created_at"],
    )
    op.create_index(
        "ix_api_access_logs_endpoint",
        "api_access_logs",
        ["endpoint"],
    )
    op.create_index(
        "ix_api_access_logs_user_id",
        "api_access_logs",
        ["user_id"],
    )
    op.create_index(
        "ix_api_access_logs_status_code",
        "api_access_logs",
        ["status_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_access_logs_status_code", table_name="api_access_logs")
    op.drop_index("ix_api_access_logs_user_id", table_name="api_access_logs")
    op.drop_index("ix_api_access_logs_endpoint", table_name="api_access_logs")
    op.drop_index("ix_api_access_logs_created_at", table_name="api_access_logs")
    op.drop_index("ix_api_access_logs_tenant_id", table_name="api_access_logs")
    op.drop_table("api_access_logs")
