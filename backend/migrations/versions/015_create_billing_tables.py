"""Create billing tables: time_entries, expenses, invoices, invoice_line_items, payments.

Revision ID: 015
Revises: 014
Create Date: 2026-06-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- invoices (must exist before time_entries/expenses FK to it) ---
    op.create_table(
        "invoices",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_number", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(10, 2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "payment_terms", sa.String(200), nullable=True, server_default="Net 30"
        ),
        sa.Column("stripe_payment_link", sa.String(500), nullable=True),
        sa.Column("stripe_payment_link_id", sa.String(255), nullable=True),
        sa.Column("qbo_invoice_id", sa.String(100), nullable=True),
        sa.Column(
            "qbo_sync_status", sa.String(50), nullable=False, server_default="pending"
        ),
        sa.Column("ledes_exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_invoices_tenant_id", "invoices", ["tenant_id"])
    op.create_index("idx_invoices_matter_id", "invoices", ["matter_id"])
    op.create_index("idx_invoices_status", "invoices", ["status"])
    op.create_index(
        "uq_invoices_tenant_number",
        "invoices",
        ["tenant_id", "invoice_number"],
        unique=True,
    )

    # --- time_entries ---
    op.create_table(
        "time_entries",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("hours", sa.Numeric(6, 2), nullable=False),
        sa.Column("hourly_rate", sa.Numeric(10, 2), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("is_billable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("utbms_task_code", sa.String(10), nullable=True),
        sa.Column("utbms_activity_code", sa.String(10), nullable=True),
        sa.Column("invoice_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
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
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_time_entries_tenant_id", "time_entries", ["tenant_id"])
    op.create_index("idx_time_entries_matter_id", "time_entries", ["matter_id"])
    op.create_index("idx_time_entries_invoice_id", "time_entries", ["invoice_id"])
    op.create_index("idx_time_entries_user_id", "time_entries", ["user_id"])

    # --- expenses ---
    op.create_table(
        "expenses",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(100), nullable=False, server_default="other"),
        sa.Column("vendor", sa.String(300), nullable=True),
        sa.Column("is_billable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("invoice_id", sa.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_expenses_tenant_id", "expenses", ["tenant_id"])
    op.create_index("idx_expenses_matter_id", "expenses", ["matter_id"])
    op.create_index("idx_expenses_invoice_id", "expenses", ["invoice_id"])

    # --- invoice_line_items ---
    op.create_table(
        "invoice_line_items",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("invoice_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(8, 2), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_invoice_line_items_invoice_id", "invoice_line_items", ["invoice_id"]
    )

    # --- payments ---
    op.create_table(
        "payments",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("method", sa.String(50), nullable=False, server_default="other"),
        sa.Column("reference_number", sa.String(200), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(255), nullable=True),
        sa.Column("qbo_payment_id", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_payments_tenant_id", "payments", ["tenant_id"])
    op.create_index("idx_payments_invoice_id", "payments", ["invoice_id"])

    # --- RLS policies ---
    for table in ("time_entries", "expenses", "invoices", "payments"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            FOR ALL TO PUBLIC
            USING (
                tenant_id::text = current_setting('app.current_tenant_id', true)
            )
            """
        )


def downgrade() -> None:
    for table in ("time_entries", "expenses", "invoices", "payments"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop dependents before invoices
    op.drop_index("idx_payments_invoice_id", table_name="payments")
    op.drop_index("idx_payments_tenant_id", table_name="payments")
    op.drop_table("payments")

    op.drop_index("idx_invoice_line_items_invoice_id", table_name="invoice_line_items")
    op.drop_table("invoice_line_items")

    op.drop_index("idx_expenses_invoice_id", table_name="expenses")
    op.drop_index("idx_expenses_matter_id", table_name="expenses")
    op.drop_index("idx_expenses_tenant_id", table_name="expenses")
    op.drop_table("expenses")

    op.drop_index("idx_time_entries_user_id", table_name="time_entries")
    op.drop_index("idx_time_entries_invoice_id", table_name="time_entries")
    op.drop_index("idx_time_entries_matter_id", table_name="time_entries")
    op.drop_index("idx_time_entries_tenant_id", table_name="time_entries")
    op.drop_table("time_entries")

    # invoices last (referenced by above tables)
    op.drop_index("uq_invoices_tenant_number", table_name="invoices")
    op.drop_index("idx_invoices_status", table_name="invoices")
    op.drop_index("idx_invoices_matter_id", table_name="invoices")
    op.drop_index("idx_invoices_tenant_id", table_name="invoices")
    op.drop_table("invoices")
