"""087 - fail-closed MCP product and credential controls.

Revision ID: 087_mcp_product_security
Revises: 086_pdf_template_sources
"""

from alembic import op
import sqlalchemy as sa


revision = "087_mcp_product_security"
down_revision = "086_pdf_template_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "stripe_subscription_status",
            sa.String(40),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "mcp_entitlement_status",
            sa.String(40),
            nullable=False,
            server_default="disabled",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "mcp_billing_status",
            sa.String(40),
            nullable=False,
            server_default="disabled",
        ),
    )
    op.create_check_constraint(
        "ck_tenants_mcp_entitlement_status",
        "tenants",
        "mcp_entitlement_status IN ('disabled', 'enabled', 'suspended')",
    )
    op.create_check_constraint(
        "ck_tenants_mcp_billing_status",
        "tenants",
        "mcp_billing_status IN ('disabled', 'active', 'past_due', 'suspended')",
    )

    # Permanently invalidate the unscoped legacy tenant key surface. Product
    # credentials live in mcp_product_keys and are always scoped and metered.
    op.execute(
        "UPDATE tenants SET api_key = NULL, api_key_hash = NULL, api_key_prefix = NULL "
        "WHERE api_key IS NOT NULL OR api_key_hash IS NOT NULL OR api_key_prefix IS NOT NULL"
    )

    # Migration owners are not guaranteed to be superusers or BYPASSRLS roles
    # (managed PostgreSQL commonly provides neither).  This table is FORCE RLS,
    # so use its existing transaction-local bypass policy for the data backfill
    # or nullable legacy rows would survive and the NOT NULL change would fail.
    op.execute("SELECT set_config('app.rls_bypass', 'on', true)")
    op.execute(
        "UPDATE mcp_product_keys SET monthly_call_limit = 1000 "
        "WHERE monthly_call_limit IS NULL"
    )
    op.execute("SELECT set_config('app.rls_bypass', 'off', true)")
    op.alter_column(
        "mcp_product_keys",
        "monthly_call_limit",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="1000",
    )
    op.add_column(
        "mcp_product_keys",
        sa.Column(
            "burst_limit_per_minute",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )
    op.create_check_constraint(
        "ck_mcp_product_keys_monthly_limit",
        "mcp_product_keys",
        "monthly_call_limit BETWEEN 1 AND 100000",
    )
    op.create_check_constraint(
        "ck_mcp_product_keys_burst_limit",
        "mcp_product_keys",
        "burst_limit_per_minute BETWEEN 1 AND 600",
    )


def downgrade() -> None:
    # Legacy key material was deliberately destroyed and cannot be restored.
    op.drop_constraint(
        "ck_mcp_product_keys_burst_limit", "mcp_product_keys", type_="check"
    )
    op.drop_constraint(
        "ck_mcp_product_keys_monthly_limit", "mcp_product_keys", type_="check"
    )
    op.drop_column("mcp_product_keys", "burst_limit_per_minute")
    op.alter_column(
        "mcp_product_keys",
        "monthly_call_limit",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=None,
    )
    op.drop_constraint("ck_tenants_mcp_billing_status", "tenants", type_="check")
    op.drop_constraint("ck_tenants_mcp_entitlement_status", "tenants", type_="check")
    op.drop_column("tenants", "mcp_billing_status")
    op.drop_column("tenants", "mcp_entitlement_status")
    op.drop_column("tenants", "stripe_subscription_status")
