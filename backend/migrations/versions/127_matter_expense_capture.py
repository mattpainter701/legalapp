"""Add receipt/import capture and accounting fields to expenses."""

from alembic import op
import sqlalchemy as sa


revision = "127_matter_expense_capture"
down_revision = "126_workspace_mcp_user_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expenses", sa.Column("client_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("expenses", sa.Column("currency", sa.String(3), nullable=False, server_default="USD"))
    op.add_column("expenses", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("expenses", sa.Column("reference_number", sa.String(100), nullable=True))
    op.add_column("expenses", sa.Column("payment_method", sa.String(30), nullable=True))
    op.add_column("expenses", sa.Column("payment_account", sa.String(100), nullable=True))
    op.add_column("expenses", sa.Column("expense_account", sa.String(100), nullable=True))
    op.add_column("expenses", sa.Column("tax_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("expenses", sa.Column("tax_code", sa.String(50), nullable=True))
    op.add_column("expenses", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("expenses", sa.Column("source_type", sa.String(30), nullable=False, server_default="manual"))
    op.add_column("expenses", sa.Column("review_status", sa.String(30), nullable=False, server_default="ready"))
    op.add_column("expenses", sa.Column("receipt_document_id", sa.UUID(as_uuid=True), nullable=True))
    op.add_column("expenses", sa.Column("source_inbound_email_id", sa.UUID(as_uuid=True), nullable=True))
    op.add_column("expenses", sa.Column("source_hash", sa.String(64), nullable=True))
    op.add_column("expenses", sa.Column("extracted_data", sa.JSON(), nullable=True))
    op.add_column("expenses", sa.Column("extraction_confidence", sa.Numeric(5, 4), nullable=True))
    op.add_column("expenses", sa.Column("qbo_vendor_id", sa.String(100), nullable=True))
    op.add_column("expenses", sa.Column("qbo_vendor_name", sa.String(300), nullable=True))
    op.add_column("expenses", sa.Column("qbo_expense_account_id", sa.String(100), nullable=True))
    op.add_column("expenses", sa.Column("qbo_expense_account_name", sa.String(300), nullable=True))
    op.add_column("expenses", sa.Column("qbo_payment_account_id", sa.String(100), nullable=True))
    op.add_column("expenses", sa.Column("qbo_payment_account_name", sa.String(300), nullable=True))
    op.add_column("expenses", sa.Column("qbo_transaction_id", sa.String(100), nullable=True))
    op.add_column("expenses", sa.Column("qbo_transaction_type", sa.String(50), nullable=True))
    op.add_column("expenses", sa.Column("qbo_sync_status", sa.String(30), nullable=True))
    op.add_column("expenses", sa.Column("qbo_sync_error", sa.Text(), nullable=True))
    op.add_column("expenses", sa.Column("qbo_synced_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key("fk_expenses_receipt_document", "expenses", "matter_documents", ["receipt_document_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_expenses_source_inbound_email", "expenses", "inbound_emails", ["source_inbound_email_id"], ["id"], ondelete="SET NULL")
    op.create_index("idx_expenses_source_inbound_email", "expenses", ["source_inbound_email_id"])
    op.create_unique_constraint("uq_expenses_tenant_source_hash", "expenses", ["tenant_id", "source_hash"])
    op.create_check_constraint("ck_expenses_currency", "expenses", "currency = upper(currency) AND char_length(currency) = 3")
    op.create_check_constraint("ck_expenses_nonnegative_amounts", "expenses", "amount >= 0 AND (client_amount IS NULL OR client_amount >= 0) AND (tax_amount IS NULL OR tax_amount >= 0)")
    op.create_check_constraint("ck_expenses_extraction_confidence", "expenses", "extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)")
    op.create_check_constraint("ck_expenses_source_type", "expenses", "source_type IN ('manual', 'email', 'graph', 'import', 'receipt', 'other')")
    op.create_check_constraint("ck_expenses_review_status", "expenses", "review_status IN ('ready', 'needs_review', 'pending', 'approved', 'rejected')")
    op.create_check_constraint("ck_expenses_qbo_sync_status", "expenses", "qbo_sync_status IS NULL OR qbo_sync_status IN ('pending', 'synced', 'error', 'skipped')")


def downgrade() -> None:
    for name in ("ck_expenses_qbo_sync_status", "ck_expenses_review_status", "ck_expenses_source_type", "ck_expenses_extraction_confidence", "ck_expenses_nonnegative_amounts", "ck_expenses_currency"):
        op.drop_constraint(name, "expenses", type_="check")
    op.drop_constraint("uq_expenses_tenant_source_hash", "expenses", type_="unique")
    op.drop_index("idx_expenses_source_inbound_email", table_name="expenses")
    op.drop_constraint("fk_expenses_source_inbound_email", "expenses", type_="foreignkey")
    op.drop_constraint("fk_expenses_receipt_document", "expenses", type_="foreignkey")
    for name in ("qbo_synced_at", "qbo_sync_error", "qbo_sync_status", "qbo_transaction_type", "qbo_transaction_id", "qbo_payment_account_name", "qbo_payment_account_id", "qbo_expense_account_name", "qbo_expense_account_id", "qbo_vendor_name", "qbo_vendor_id", "extraction_confidence", "extracted_data", "source_hash", "source_inbound_email_id", "receipt_document_id", "review_status", "source_type", "notes", "tax_code", "tax_amount", "expense_account", "payment_account", "payment_method", "reference_number", "due_date", "currency", "client_amount"):
        op.drop_column("expenses", name)
