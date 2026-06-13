"""055 — Firm branding fields on tenant_settings (Task 1303 branding).

Adds nullable firm-branding columns to ``tenant_settings`` so each tenant can
customize the firm name, logo, contact details, and PDF footer/disclaimer
used on branded exports (e.g. the trust ledger statement PDF). All columns
are nullable — when unset, callers fall back to ``Tenant.name`` /
``Tenant.address``. No RLS changes needed (tenant_settings RLS already
covers this table).
"""

from alembic import op
import sqlalchemy as sa

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_settings",
        sa.Column("firm_name", sa.String(300), nullable=True),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("firm_logo_url", sa.String(1000), nullable=True),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("firm_address", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("firm_phone", sa.String(50), nullable=True),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("firm_email", sa.String(320), nullable=True),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("firm_website", sa.String(300), nullable=True),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("firm_pdf_footer", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_settings", "firm_pdf_footer")
    op.drop_column("tenant_settings", "firm_website")
    op.drop_column("tenant_settings", "firm_email")
    op.drop_column("tenant_settings", "firm_phone")
    op.drop_column("tenant_settings", "firm_address")
    op.drop_column("tenant_settings", "firm_logo_url")
    op.drop_column("tenant_settings", "firm_name")
