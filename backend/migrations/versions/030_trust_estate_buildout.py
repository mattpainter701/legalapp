"""030 — Trust & Estate build-out: expand estates + administration sub-tables.

Revision ID: 030
Revises: 029
Create Date: 2026-06-04

Expands the skeleton ``estates`` table into a full estate-administration module:
  - Adds estate_name, matter_id, client_contact_id, jurisdiction, domicile_state,
    date_of_death, court_name, case_number, gross_estate_value, net_estate_value,
    representative_type.
  - Creates seven tenant-isolated (RLS) sub-tables: fiduciaries, beneficiaries,
    assets, liabilities (creditor claims), distributions, deadlines, and the
    fiduciary accounting ledger.

Documents reuse the existing ``matter_documents`` table via ``estates.matter_id``.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def _id_col():
    return sa.Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _tenant_col():
    return sa.Column("tenant_id", UUID(as_uuid=True), nullable=False)


def _estate_fk():
    return sa.Column(
        "estate_id",
        UUID(as_uuid=True),
        sa.ForeignKey("estates.id", ondelete="CASCADE"),
        nullable=False,
    )


def _timestamps():
    return (
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
    )


def _enable_rls(table: str) -> None:
    op.create_index(f"idx_{table}_tenant_id", table, ["tenant_id"])
    op.create_index(f"idx_{table}_estate_id", table, ["estate_id"])
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """
    )


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ── Expand estates ────────────────────────────────────────────────────────
    op.add_column("estates", sa.Column("estate_name", sa.String(500), nullable=True))
    op.add_column(
        "estates",
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "estates",
        sa.Column(
            "client_contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("estates", sa.Column("jurisdiction", sa.String(300), nullable=True))
    op.add_column("estates", sa.Column("domicile_state", sa.String(100), nullable=True))
    op.add_column("estates", sa.Column("date_of_death", sa.Date(), nullable=True))
    op.add_column("estates", sa.Column("court_name", sa.String(300), nullable=True))
    op.add_column("estates", sa.Column("case_number", sa.String(100), nullable=True))
    op.add_column(
        "estates", sa.Column("gross_estate_value", sa.Numeric(14, 2), nullable=True)
    )
    op.add_column(
        "estates", sa.Column("net_estate_value", sa.Numeric(14, 2), nullable=True)
    )
    op.add_column(
        "estates", sa.Column("representative_type", sa.String(100), nullable=True)
    )
    # Backfill estate_name from the legacy title column.
    op.execute("UPDATE estates SET estate_name = title WHERE estate_name IS NULL")
    op.create_index("idx_estates_matter_id", "estates", ["matter_id"])
    op.create_index("idx_estates_client_contact_id", "estates", ["client_contact_id"])

    # ── estate_fiduciaries ────────────────────────────────────────────────────
    op.create_table(
        "estate_fiduciaries",
        _id_col(),
        _tenant_col(),
        _estate_fk(),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="executor"),
        sa.Column("appointment_date", sa.Date(), nullable=True),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("compensation_basis", sa.String(100), nullable=True),
        sa.Column("compensation_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("email", sa.String(300), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    _enable_rls("estate_fiduciaries")

    # ── estate_beneficiaries ──────────────────────────────────────────────────
    op.create_table(
        "estate_beneficiaries",
        _id_col(),
        _tenant_col(),
        _estate_fk(),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("relationship", sa.String(150), nullable=True),
        sa.Column(
            "beneficiary_type",
            sa.String(50),
            nullable=False,
            server_default="residuary",
        ),
        sa.Column("share_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("bequest_description", sa.Text(), nullable=True),
        sa.Column(
            "is_charity", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("charity_ein", sa.String(20), nullable=True),
        sa.Column("email", sa.String(300), nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column(
            "distribution_status",
            sa.String(50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    _enable_rls("estate_beneficiaries")

    # ── estate_assets ─────────────────────────────────────────────────────────
    op.create_table(
        "estate_assets",
        _id_col(),
        _tenant_col(),
        _estate_fk(),
        sa.Column("name", sa.String(400), nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="other"),
        sa.Column("ownership_type", sa.String(50), nullable=True),
        sa.Column("date_of_death_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("current_value", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "is_probate", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("institution", sa.String(300), nullable=True),
        sa.Column("account_number_masked", sa.String(10), nullable=True),
        sa.Column("valuation_date", sa.Date(), nullable=True),
        sa.Column("location", sa.String(300), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    _enable_rls("estate_assets")

    # ── estate_liabilities (creditor claims & expenses) ───────────────────────
    op.create_table(
        "estate_liabilities",
        _id_col(),
        _tenant_col(),
        _estate_fk(),
        sa.Column("creditor_name", sa.String(400), nullable=False),
        sa.Column("claim_type", sa.String(50), nullable=False, server_default="debt"),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("date_filed", sa.Date(), nullable=True),
        sa.Column("bar_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    _enable_rls("estate_liabilities")

    # ── estate_distributions ──────────────────────────────────────────────────
    op.create_table(
        "estate_distributions",
        _id_col(),
        _tenant_col(),
        _estate_fk(),
        sa.Column(
            "beneficiary_id",
            UUID(as_uuid=True),
            sa.ForeignKey("estate_beneficiaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("estate_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column(
            "distribution_type",
            sa.String(50),
            nullable=False,
            server_default="interim",
        ),
        sa.Column("distribution_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="planned"),
        sa.Column("check_number", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    _enable_rls("estate_distributions")

    # ── estate_deadlines (tax filings, court dates, tasks) ────────────────────
    op.create_table(
        "estate_deadlines",
        _id_col(),
        _tenant_col(),
        _estate_fk(),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column(
            "deadline_type", sa.String(50), nullable=False, server_default="other"
        ),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column(
            "assigned_to",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    _enable_rls("estate_deadlines")
    op.create_index(
        "idx_estate_deadlines_due_date", "estate_deadlines", ["tenant_id", "due_date"]
    )

    # ── estate_accounting_entries (fiduciary accounting ledger) ───────────────
    op.create_table(
        "estate_accounting_entries",
        _id_col(),
        _tenant_col(),
        _estate_fk(),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column(
            "entry_type", sa.String(50), nullable=False, server_default="receipt"
        ),
        sa.Column(
            "account_class", sa.String(50), nullable=False, server_default="principal"
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("payee_payor", sa.String(300), nullable=True),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("estate_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reference_number", sa.String(200), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    _enable_rls("estate_accounting_entries")


def downgrade() -> None:
    for table in (
        "estate_accounting_entries",
        "estate_deadlines",
        "estate_distributions",
        "estate_liabilities",
        "estate_assets",
        "estate_beneficiaries",
        "estate_fiduciaries",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)

    op.drop_index("idx_estates_client_contact_id", "estates")
    op.drop_index("idx_estates_matter_id", "estates")
    for col in (
        "representative_type",
        "net_estate_value",
        "gross_estate_value",
        "case_number",
        "court_name",
        "date_of_death",
        "domicile_state",
        "jurisdiction",
        "client_contact_id",
        "matter_id",
        "estate_name",
    ):
        op.drop_column("estates", col)
