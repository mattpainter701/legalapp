"""071 - operator audit logs

Revision ID: 071_operator_audit_logs
Revises: 070_mcp_product_gateway
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "071_operator_audit_logs"
down_revision = "070_mcp_product_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column(
            "actor_type",
            sa.String(length=40),
            nullable=False,
            server_default="platform_key",
        ),
        sa.Column("actor_id", sa.String(length=120), nullable=True),
        sa.Column("resource_type", sa.String(length=120), nullable=True),
        sa.Column("resource_id", sa.String(length=120), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_operator_audit_logs_action", "operator_audit_logs", ["action"])
    op.create_index(
        "ix_operator_audit_logs_created_at", "operator_audit_logs", ["created_at"]
    )
    op.create_index(
        "ix_operator_audit_logs_resource",
        "operator_audit_logs",
        ["resource_type", "resource_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_operator_audit_logs_resource", table_name="operator_audit_logs")
    op.drop_index("ix_operator_audit_logs_created_at", table_name="operator_audit_logs")
    op.drop_index("ix_operator_audit_logs_action", table_name="operator_audit_logs")
    op.drop_table("operator_audit_logs")
