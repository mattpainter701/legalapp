"""Add dedicated client CRM profile, consent, and billing fields.

Revision ID: 111_client_crm_management
Revises: 110_chat_artifact_pipeline
"""

from alembic import op
import sqlalchemy as sa


revision = "111_client_crm_management"
down_revision = "110_chat_artifact_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts", sa.Column("preferred_name", sa.String(200), nullable=True)
    )
    op.add_column("contacts", sa.Column("date_of_birth", sa.Date(), nullable=True))
    op.add_column("contacts", sa.Column("client_number", sa.String(100), nullable=True))
    op.add_column("contacts", sa.Column("client_status", sa.String(50), nullable=True))
    op.add_column(
        "contacts", sa.Column("preferred_contact_method", sa.String(50), nullable=True)
    )
    op.add_column(
        "contacts", sa.Column("preferred_language", sa.String(100), nullable=True)
    )
    op.add_column("contacts", sa.Column("emergency_contact", sa.JSON(), nullable=True))
    op.add_column(
        "contacts",
        sa.Column(
            "sms_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "contacts",
        sa.Column("sms_opt_in_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column(
            "email_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.add_column(
        "contacts", sa.Column("referral_source", sa.String(300), nullable=True)
    )
    op.add_column(
        "contacts",
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contacts", sa.Column("preferred_payment_method", sa.String(50), nullable=True)
    )
    op.add_column(
        "contacts",
        sa.Column(
            "billing_delivery_method",
            sa.String(50),
            nullable=False,
            server_default="email",
        ),
    )
    op.add_column(
        "contacts",
        sa.Column(
            "payment_terms_days", sa.Integer(), nullable=False, server_default="30"
        ),
    )
    op.add_column("contacts", sa.Column("billing_notes", sa.Text(), nullable=True))
    op.add_column(
        "contacts", sa.Column("qbo_customer_id", sa.String(100), nullable=True)
    )
    op.add_column(
        "contacts", sa.Column("qbo_sync_token", sa.String(100), nullable=True)
    )
    op.add_column(
        "contacts",
        sa.Column("qbo_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contacts", sa.Column("stripe_customer_id", sa.String(255), nullable=True)
    )

    op.execute(
        """
        UPDATE contacts
        SET client_status = CASE
            WHEN contact_type = 'prospect' THEN 'prospect'
            WHEN contact_type = 'client' THEN 'active'
            ELSE NULL
        END
        WHERE client_status IS NULL
        """
    )

    op.create_index(
        "idx_contacts_tenant_client_status", "contacts", ["tenant_id", "client_status"]
    )
    op.create_index(
        "idx_contacts_tenant_qbo_customer", "contacts", ["tenant_id", "qbo_customer_id"]
    )
    op.create_index(
        "uq_contacts_tenant_client_number",
        "contacts",
        ["tenant_id", "client_number"],
        unique=True,
        postgresql_where=sa.text("client_number IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_contacts_client_status",
        "contacts",
        "client_status IS NULL OR client_status IN ('prospect', 'active', 'inactive', 'former')",
    )
    op.create_check_constraint(
        "ck_contacts_preferred_contact_method",
        "contacts",
        "preferred_contact_method IS NULL OR preferred_contact_method IN ('email', 'phone', 'sms', 'mail', 'portal')",
    )
    op.create_check_constraint(
        "ck_contacts_preferred_payment_method",
        "contacts",
        "preferred_payment_method IS NULL OR preferred_payment_method IN ('stripe', 'check', 'ach', 'wire', 'cash', 'other')",
    )
    op.create_check_constraint(
        "ck_contacts_billing_delivery_method",
        "contacts",
        "billing_delivery_method IN ('email', 'mail', 'portal')",
    )
    op.create_check_constraint(
        "ck_contacts_payment_terms_days",
        "contacts",
        "payment_terms_days BETWEEN 0 AND 365",
    )

    # contacts already has a strict tenant policy. Reassert FORCE so the new
    # PII-bearing columns remain protected even when the runtime role owns the table.
    op.execute("ALTER TABLE contacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE contacts FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for constraint in (
        "ck_contacts_payment_terms_days",
        "ck_contacts_billing_delivery_method",
        "ck_contacts_preferred_payment_method",
        "ck_contacts_preferred_contact_method",
        "ck_contacts_client_status",
    ):
        op.drop_constraint(constraint, "contacts", type_="check")
    op.drop_index("uq_contacts_tenant_client_number", table_name="contacts")
    op.drop_index("idx_contacts_tenant_qbo_customer", table_name="contacts")
    op.drop_index("idx_contacts_tenant_client_status", table_name="contacts")
    for column in (
        "stripe_customer_id",
        "qbo_synced_at",
        "qbo_sync_token",
        "qbo_customer_id",
        "billing_notes",
        "payment_terms_days",
        "billing_delivery_method",
        "preferred_payment_method",
        "last_contacted_at",
        "referral_source",
        "email_opt_in",
        "sms_opt_in_at",
        "sms_opt_in",
        "emergency_contact",
        "preferred_language",
        "preferred_contact_method",
        "client_status",
        "client_number",
        "date_of_birth",
        "preferred_name",
    ):
        op.drop_column("contacts", column)
