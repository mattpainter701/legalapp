"""Create tenant_settings table for per-tenant configuration management.

Revision ID: 014
Revises: 013
Create Date: 2026-06-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("cache_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "cache_ttl_multiplier", sa.Float(), nullable=False, server_default="1.0"
        ),
        sa.Column(
            "default_expertise_level",
            sa.String(50),
            nullable=False,
            server_default="mid",
        ),
        sa.Column(
            "default_practice_areas", sa.JSON(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "default_privacy_mode", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "enable_auto_memory", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "enable_pii_detection", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "enable_skill_routing", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "enable_matter_context", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("max_requests_per_minute", sa.Integer(), nullable=True),
        sa.Column("max_daily_tokens", sa.Integer(), nullable=True),
        sa.Column("custom_config", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_tenant_settings_tenant_id",
        "tenant_settings",
        ["tenant_id"],
        unique=True,
    )
    op.create_index(
        "idx_tenant_settings_tenant_id",
        "tenant_settings",
        ["tenant_id"],
    )

    op.execute("ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_tenant_settings ON tenant_settings
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_tenant_settings ON tenant_settings"
    )
    op.execute("ALTER TABLE tenant_settings DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_tenant_settings_tenant_id", table_name="tenant_settings")
    op.drop_index("uq_tenant_settings_tenant_id", table_name="tenant_settings")
    op.drop_table("tenant_settings")
