"""035 — Structured plugin setup records.

Revision ID: 035
Revises: 034
Create Date: 2026-06-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "035"
down_revision = "034b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_plugin_setups",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("plugin_name", sa.String(100), nullable=False),
        sa.Column("jurisdictions", sa.JSON(), nullable=True),
        sa.Column("escalation_rules", sa.JSON(), nullable=True),
        sa.Column("approval_thresholds", sa.JSON(), nullable=True),
        sa.Column("template_preferences", sa.JSON(), nullable=True),
        sa.Column("cloud_bindings", sa.JSON(), nullable=True),
        sa.Column("calendar_bindings", sa.JSON(), nullable=True),
        sa.Column("house_style", sa.JSON(), nullable=True),
        sa.Column("custom_config", sa.JSON(), nullable=True),
        sa.Column("generated_profile", sa.Text(), nullable=True),
        sa.Column("setup_health", sa.JSON(), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        sa.UniqueConstraint("tenant_id", "plugin_name", name="uq_tenant_plugin_setup"),
    )
    op.create_index(
        "idx_tenant_plugin_setups_tenant", "tenant_plugin_setups", ["tenant_id"]
    )
    op.create_index(
        "idx_tenant_plugin_setups_plugin", "tenant_plugin_setups", ["plugin_name"]
    )
    op.execute("ALTER TABLE tenant_plugin_setups ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_plugin_setups FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_tenant_plugin_setups
        ON tenant_plugin_setups
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_tenant_plugin_setups "
        "ON tenant_plugin_setups"
    )
    op.execute("ALTER TABLE tenant_plugin_setups DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_tenant_plugin_setups_plugin", table_name="tenant_plugin_setups")
    op.drop_index("idx_tenant_plugin_setups_tenant", table_name="tenant_plugin_setups")
    op.drop_table("tenant_plugin_setups")
