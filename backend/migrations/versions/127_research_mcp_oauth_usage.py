"""Correlate Research MCP OAuth usage with durable consent grants.

Revision ID: 127_research_mcp_oauth_usage
Revises: 126_workspace_mcp_user_access
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "127_research_mcp_oauth_usage"
down_revision = "126_workspace_mcp_user_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_usage_events",
        sa.Column("oauth_grant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_mcp_usage_events_oauth_grant_id",
        "mcp_usage_events",
        "workspace_mcp_grants",
        ["oauth_grant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_mcp_usage_events_oauth_grant_id",
        "mcp_usage_events",
        ["oauth_grant_id"],
    )
    op.create_index(
        "ix_mcp_usage_events_oauth_grant_created",
        "mcp_usage_events",
        ["oauth_grant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_usage_events_oauth_grant_created", table_name="mcp_usage_events"
    )
    op.drop_index("ix_mcp_usage_events_oauth_grant_id", table_name="mcp_usage_events")
    op.drop_constraint(
        "fk_mcp_usage_events_oauth_grant_id",
        "mcp_usage_events",
        type_="foreignkey",
    )
    op.drop_column("mcp_usage_events", "oauth_grant_id")
