"""Add reusable AI routing profiles and tenant assignment.

Revision ID: 125_llm_routing_profiles
Revises: 124_inbound_email
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "125_llm_routing_profiles"
down_revision = "124_inbound_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_routing_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("standard_route", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("premium_route", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("standard_allow_matter_context", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("premium_allow_matter_context", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("activation", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_llm_routing_profiles_name"),
    )
    op.create_index(
        "uq_llm_routing_profiles_default",
        "llm_routing_profiles",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.add_column("tenant_settings", sa.Column("llm_routing_profile_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_tenant_settings_llm_routing_profile", "tenant_settings", "llm_routing_profiles", ["llm_routing_profile_id"], ["id"], ondelete="SET NULL")
    op.execute("""
        INSERT INTO llm_routing_profiles (
            name, description, standard_route, premium_route,
            standard_allow_matter_context, premium_allow_matter_context,
            is_default, is_active, activation
        )
        SELECT 'Default', 'Migrated active platform routing profile',
               COALESCE(value->'standard', '{}'::json), COALESCE(value->'premium', '{}'::json),
               COALESCE((value->'standard'->>'allow_matter_context')::boolean, false),
               true, true, true, value->'activation'
        FROM platform_settings WHERE key = 'llm_route_config_v2'
        UNION ALL
        SELECT 'Default', 'Default platform routing profile', '{}'::json, '{}'::json,
               false, true, true, true, NULL
        WHERE NOT EXISTS (SELECT 1 FROM platform_settings WHERE key = 'llm_route_config_v2')
    """)


def downgrade() -> None:
    op.drop_constraint("fk_tenant_settings_llm_routing_profile", "tenant_settings", type_="foreignkey")
    op.drop_column("tenant_settings", "llm_routing_profile_id")
    op.drop_index("uq_llm_routing_profiles_default", table_name="llm_routing_profiles")
    op.drop_table("llm_routing_profiles")
