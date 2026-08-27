"""Add explicit QBO invoice billing state and A/R account mapping.

Revision ID: 136_qbo_invoice_billing
Revises: 135_conflict_invoice_audit
"""

from alembic import op
import sqlalchemy as sa


revision = "136_qbo_invoice_billing"
down_revision = "135_conflict_invoice_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("qbo_sync_token", sa.String(100), nullable=True))
    op.add_column("invoices", sa.Column("qbo_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoices", sa.Column("qbo_sync_error", sa.Text(), nullable=True))
    op.add_column("invoices", sa.Column("billed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("qbo_integrations", sa.Column("qbo_ar_account_id", sa.String(100), nullable=True))
    op.add_column("qbo_integrations", sa.Column("qbo_ar_account_name", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("qbo_integrations", "qbo_ar_account_name")
    op.drop_column("qbo_integrations", "qbo_ar_account_id")
    op.drop_column("invoices", "billed_at")
    op.drop_column("invoices", "qbo_sync_error")
    op.drop_column("invoices", "qbo_synced_at")
    op.drop_column("invoices", "qbo_sync_token")