"""Add a per-firm currency code for branded amounts.

The client portal puts money on screen (balances, invoices) for the firm's own
clients. Before this the frontend hardcoded USD, so every amount a non-US firm
rendered was labelled in the wrong currency while the adjacent dates followed
the browser locale. The column is nullable and resolved to USD in
``get_firm_branding`` when unset, so existing tenants and all current invoice
behaviour are unchanged until a firm sets its own code.

Revision ID: 174_firm_currency
Revises: 173_rls_tenant_guc_nullif
"""

import sqlalchemy as sa
from alembic import op

revision = "174_firm_currency"
down_revision = "173_rls_tenant_guc_nullif"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tenant_settings",
        sa.Column("firm_currency", sa.String(length=3), nullable=True),
    )


def downgrade():
    op.drop_column("tenant_settings", "firm_currency")
