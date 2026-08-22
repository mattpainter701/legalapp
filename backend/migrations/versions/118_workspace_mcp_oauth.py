"""Add workspace MCP OAuth clients and tamper-evident audit events.

Revision ID: 118_workspace_mcp_oauth
Revises: 117_demo_purge_claim
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "118_workspace_mcp_oauth"
down_revision = "117_demo_purge_claim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_mcp_clients",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("client_id", sa.String(200), nullable=False),
        sa.Column("client_name", sa.String(200), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("grant_types", sa.JSON(), nullable=False),
        sa.Column("response_types", sa.JSON(), nullable=False),
        sa.Column(
            "token_endpoint_auth_method",
            sa.String(40),
            nullable=False,
            server_default="none",
        ),
        sa.Column("software_id", sa.String(200), nullable=True),
        sa.Column("software_version", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_workspace_mcp_clients_status"
        ),
        sa.CheckConstraint(
            "json_typeof(redirect_uris) = 'array'",
            name="ck_workspace_mcp_clients_redirect_uris_array",
        ),
        sa.CheckConstraint(
            "json_typeof(grant_types) = 'array'",
            name="ck_workspace_mcp_clients_grant_types_array",
        ),
        sa.CheckConstraint(
            "json_typeof(response_types) = 'array'",
            name="ck_workspace_mcp_clients_response_types_array",
        ),
        sa.UniqueConstraint("client_id", name="uq_workspace_mcp_clients_client_id"),
    )
    op.create_index(
        "ix_workspace_mcp_clients_status_created",
        "workspace_mcp_clients",
        ["status", "created_at"],
    )

    op.create_table(
        "workspace_mcp_audit_events",
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
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "grant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_mcp_grants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("client_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("tool_name", sa.String(120), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("request_id", sa.String(200), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column(
            "metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column("chain_position", sa.BigInteger(), nullable=False),
        sa.Column("prev_event_hash", sa.String(64), nullable=True),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'denied', 'error')",
            name="ck_workspace_mcp_audit_outcome",
        ),
        sa.CheckConstraint(
            "chain_position > 0", name="ck_workspace_mcp_audit_chain_position"
        ),
        sa.CheckConstraint(
            "prev_event_hash IS NULL OR prev_event_hash ~ '^[0-9a-f]{64}$'",
            name="ck_workspace_mcp_audit_prev_hash",
        ),
        sa.CheckConstraint(
            "event_hash ~ '^[0-9a-f]{64}$'", name="ck_workspace_mcp_audit_event_hash"
        ),
        sa.UniqueConstraint(
            "tenant_id", "chain_position", name="uq_workspace_mcp_audit_tenant_position"
        ),
        sa.UniqueConstraint(
            "tenant_id", "event_hash", name="uq_workspace_mcp_audit_tenant_hash"
        ),
    )
    op.create_index(
        "ix_workspace_mcp_audit_tenant_created",
        "workspace_mcp_audit_events",
        ["tenant_id", "created_at", "id"],
    )
    op.create_index(
        "ix_workspace_mcp_audit_tenant_grant_created",
        "workspace_mcp_audit_events",
        ["tenant_id", "grant_id", "created_at"],
    )
    op.execute("ALTER TABLE workspace_mcp_audit_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_mcp_audit_events_tenant_isolation
        ON workspace_mcp_audit_events
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE workspace_mcp_audit_events FORCE ROW LEVEL SECURITY")

    op.create_index(
        "uq_workspace_mcp_grants_one_active_client",
        "workspace_mcp_grants",
        ["tenant_id", "user_id", "client_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_workspace_mcp_grants_one_active_client", table_name="workspace_mcp_grants"
    )
    op.drop_index(
        "ix_workspace_mcp_audit_tenant_grant_created",
        table_name="workspace_mcp_audit_events",
    )
    op.drop_index(
        "ix_workspace_mcp_audit_tenant_created", table_name="workspace_mcp_audit_events"
    )
    op.drop_table("workspace_mcp_audit_events")
    op.drop_index(
        "ix_workspace_mcp_clients_status_created", table_name="workspace_mcp_clients"
    )
    op.drop_table("workspace_mcp_clients")
