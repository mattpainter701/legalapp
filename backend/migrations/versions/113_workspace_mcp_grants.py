"""Add durable end-user consent grants for workspace MCP.

Revision ID: 113_workspace_mcp_grants
Revises: 112_client_account_relationships
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "113_workspace_mcp_grants"
down_revision = "112_client_account_relationships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_mcp_grants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_id", sa.String(200), nullable=False),
        sa.Column("client_name", sa.String(200), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("consent_version", sa.String(50), nullable=False),
        sa.Column("consent_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_workspace_mcp_grants_status",
        ),
    )
    op.create_index(
        "idx_workspace_mcp_grants_tenant_user_status",
        "workspace_mcp_grants",
        ["tenant_id", "user_id", "status"],
    )
    op.create_index(
        "idx_workspace_mcp_grants_tenant_client_status",
        "workspace_mcp_grants",
        ["tenant_id", "client_id", "status"],
    )
    op.execute("ALTER TABLE workspace_mcp_grants ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_mcp_grants_tenant_isolation
        ON workspace_mcp_grants
        USING (
            tenant_id = NULLIF(
                current_setting('app.current_tenant_id', true), ''
            )::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.current_tenant_id', true), ''
            )::uuid
        )
        """
    )
    op.execute("ALTER TABLE workspace_mcp_grants FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index(
        "idx_workspace_mcp_grants_tenant_client_status",
        table_name="workspace_mcp_grants",
    )
    op.drop_index(
        "idx_workspace_mcp_grants_tenant_user_status",
        table_name="workspace_mcp_grants",
    )
    op.drop_table("workspace_mcp_grants")
