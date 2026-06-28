"""070 - MCP product keys and usage events

Revision ID: 070_mcp_product_gateway
Revises: 069_rbac_rls
Create Date: 2026-06-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "070_mcp_product_gateway"
down_revision = "069_rbac_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_product_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=True),
        sa.Column("monthly_call_limit", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mcp_product_keys_tenant_id", "mcp_product_keys", ["tenant_id"])
    op.create_index("ix_mcp_product_keys_key_hash", "mcp_product_keys", ["key_hash"], unique=True)
    op.create_index("ix_mcp_product_keys_key_prefix", "mcp_product_keys", ["key_prefix"])

    op.create_table(
        "mcp_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_key_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mcp_product_keys.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("auth_type", sa.String(length=40), nullable=False),
        sa.Column("transport", sa.String(length=40), nullable=False, server_default="rest"),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("error_class", sa.String(length=120), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mcp_usage_events_tenant_id", "mcp_usage_events", ["tenant_id"])
    op.create_index("ix_mcp_usage_events_product_key_id", "mcp_usage_events", ["product_key_id"])
    op.create_index("ix_mcp_usage_events_created_at", "mcp_usage_events", ["created_at"])
    op.create_index("ix_mcp_usage_events_tenant_created", "mcp_usage_events", ["tenant_id", "created_at"])
    op.create_index("ix_mcp_usage_events_key_created", "mcp_usage_events", ["product_key_id", "created_at"])

    for table in ("mcp_product_keys", "mcp_usage_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_rls_bypass ON {table}
            USING (current_setting('app.rls_bypass', true) = 'on')
            WITH CHECK (current_setting('app.rls_bypass', true) = 'on')
            """
        )


def downgrade() -> None:
    for table in ("mcp_usage_events", "mcp_product_keys"):
        op.execute(f"DROP POLICY IF EXISTS {table}_rls_bypass ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index("ix_mcp_usage_events_key_created", table_name="mcp_usage_events")
    op.drop_index("ix_mcp_usage_events_tenant_created", table_name="mcp_usage_events")
    op.drop_index("ix_mcp_usage_events_created_at", table_name="mcp_usage_events")
    op.drop_index("ix_mcp_usage_events_product_key_id", table_name="mcp_usage_events")
    op.drop_index("ix_mcp_usage_events_tenant_id", table_name="mcp_usage_events")
    op.drop_table("mcp_usage_events")
    op.drop_index("ix_mcp_product_keys_key_prefix", table_name="mcp_product_keys")
    op.drop_index("ix_mcp_product_keys_key_hash", table_name="mcp_product_keys")
    op.drop_index("ix_mcp_product_keys_tenant_id", table_name="mcp_product_keys")
    op.drop_table("mcp_product_keys")

