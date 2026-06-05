"""034b — Plugin platform phase 1: entitlements and matter plugin binding.

Revision ID: 034b
Revises: 034
Create Date: 2026-06-04

Changes:
  + tenant_plugin_entitlements for tenant-level plugin purchase/trial state
  + matters.primary_plugin and matters.plugin_workflow_state
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "034b"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_plugin_entitlements",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("plugin_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="purchased"),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("seat_limit", sa.Integer(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "plugin_name", name="uq_tenant_plugin_entitlement"
        ),
    )
    op.create_index(
        "idx_tenant_plugin_entitlements_tenant",
        "tenant_plugin_entitlements",
        ["tenant_id"],
    )
    op.create_index(
        "idx_tenant_plugin_entitlements_plugin",
        "tenant_plugin_entitlements",
        ["plugin_name"],
    )
    op.execute("ALTER TABLE tenant_plugin_entitlements ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_plugin_entitlements FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_tenant_plugin_entitlements
        ON tenant_plugin_entitlements
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    op.add_column("matters", sa.Column("primary_plugin", sa.String(100), nullable=True))
    op.add_column(
        "matters", sa.Column("plugin_workflow_state", sa.JSON(), nullable=True)
    )
    op.create_index("idx_matters_primary_plugin", "matters", ["primary_plugin"])


def downgrade() -> None:
    op.drop_index("idx_matters_primary_plugin", table_name="matters")
    op.drop_column("matters", "plugin_workflow_state")
    op.drop_column("matters", "primary_plugin")

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_tenant_plugin_entitlements "
        "ON tenant_plugin_entitlements"
    )
    op.execute("ALTER TABLE tenant_plugin_entitlements DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "idx_tenant_plugin_entitlements_plugin",
        table_name="tenant_plugin_entitlements",
    )
    op.drop_index(
        "idx_tenant_plugin_entitlements_tenant",
        table_name="tenant_plugin_entitlements",
    )
    op.drop_table("tenant_plugin_entitlements")
