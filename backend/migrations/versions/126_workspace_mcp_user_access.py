"""Add tenant-administered Workspace MCP user access.

Revision ID: 126_workspace_mcp_user_access
Revises: 125_llm_routing_profiles
"""

from alembic import op
import sqlalchemy as sa


revision = "126_workspace_mcp_user_access"
down_revision = "125_llm_routing_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "workspace_mcp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "tenant_settings",
        sa.Column(
            "default_workspace_mcp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_settings", "default_workspace_mcp_enabled")
    op.drop_column("users", "workspace_mcp_enabled")
