"""Add the tenant-wide Workspace MCP administrative gate."""

from alembic import op
import sqlalchemy as sa

revision = "128_workspace_mcp_tenant_gate"
down_revision = (
    "127_research_mcp_oauth_usage",
    "127_matter_expense_capture",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_settings",
        sa.Column(
            "workspace_mcp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_settings", "workspace_mcp_enabled")
