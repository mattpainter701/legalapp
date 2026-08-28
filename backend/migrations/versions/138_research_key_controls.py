"""Add customer-managed Research MCP key controls.

Revision ID: 138_research_key_controls
Revises: 137_background_ai_value_quota
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "138_research_key_controls"
down_revision = "137_background_ai_value_quota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_product_keys", sa.Column("purpose", sa.String(255), nullable=True)
    )
    op.add_column(
        "mcp_product_keys",
        sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_mcp_product_keys_assigned_to_user_id",
        "mcp_product_keys",
        "users",
        ["assigned_to_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_mcp_product_keys_assigned_to_user_id",
        "mcp_product_keys",
        ["assigned_to_user_id"],
    )
    op.add_column(
        "mcp_product_keys",
        sa.Column("monthly_budget_cents", sa.Integer(), nullable=True),
    )
    op.add_column(
        "mcp_product_keys",
        sa.Column(
            "unit_price_cents", sa.Integer(), nullable=False, server_default="45"
        ),
    )
    op.add_column(
        "mcp_product_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_mcp_product_keys_expires_at", "mcp_product_keys", ["expires_at"]
    )
    op.create_check_constraint(
        "ck_mcp_product_keys_monthly_budget",
        "mcp_product_keys",
        "monthly_budget_cents IS NULL OR monthly_budget_cents >= unit_price_cents",
    )
    op.create_check_constraint(
        "ck_mcp_product_keys_unit_price",
        "mcp_product_keys",
        "unit_price_cents > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mcp_product_keys_unit_price", "mcp_product_keys", type_="check"
    )
    op.drop_constraint(
        "ck_mcp_product_keys_monthly_budget", "mcp_product_keys", type_="check"
    )
    op.drop_index("ix_mcp_product_keys_expires_at", table_name="mcp_product_keys")
    op.drop_column("mcp_product_keys", "expires_at")
    op.drop_column("mcp_product_keys", "unit_price_cents")
    op.drop_column("mcp_product_keys", "monthly_budget_cents")
    op.drop_index(
        "ix_mcp_product_keys_assigned_to_user_id", table_name="mcp_product_keys"
    )
    op.drop_constraint(
        "fk_mcp_product_keys_assigned_to_user_id",
        "mcp_product_keys",
        type_="foreignkey",
    )
    op.drop_column("mcp_product_keys", "assigned_to_user_id")
    op.drop_column("mcp_product_keys", "purpose")
