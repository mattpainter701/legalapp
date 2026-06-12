"""051 — Domestic relations (family law) module build-out.

Creates the domestic-relations module: a ``domestic_cases`` parent plus
tenant-isolated (RLS) sub-tables for parties, children, custody arrangements,
support orders, the payment ledger, saved child-support calculation runs,
deadlines, and an activity log.

The child-support calculator engine (app.services.childsupport) is stateless;
``child_support_calculations`` persists each run's input snapshot + worksheet so
results are reproducible and auditable. Documents/billing reuse the existing
Matter via ``domestic_cases.matter_id``.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "051"
down_revision = "050"
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


def _case_fk():
    return sa.Column(
        "case_id",
        UUID(as_uuid=True),
        sa.ForeignKey("domestic_cases.id", ondelete="CASCADE"),
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


def _party_fk(name: str, nullable: bool = True):
    return sa.Column(
        name,
        UUID(as_uuid=True),
        sa.ForeignKey("domestic_parties.id", ondelete="SET NULL"),
        nullable=nullable,
    )


def _enable_rls(table: str, with_case_index: bool = True) -> None:
    op.create_index(f"idx_{table}_tenant_id", table, ["tenant_id"])
    if with_case_index:
        op.create_index(f"idx_{table}_case_id", table, ["case_id"])
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """
    )


_SUBTABLES = [
    "domestic_events",
    "domestic_deadlines",
    "child_support_calculations",
    "support_payments",
    "support_orders",
    "custody_arrangements",
    "domestic_children",
    "domestic_parties",
]


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ── domestic_cases ────────────────────────────────────────────────────────
    op.create_table(
        "domestic_cases",
        _id_col(),
        _tenant_col(),
        sa.Column("case_name", sa.String(500), nullable=False),
        sa.Column("case_type", sa.String(50), server_default="support"),
        sa.Column("status", sa.String(50), server_default="active"),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("jurisdiction", sa.String(2), server_default="ND"),
        sa.Column("county", sa.String(120), nullable=True),
        sa.Column("court_name", sa.String(300), nullable=True),
        sa.Column("case_number", sa.String(100), nullable=True),
        sa.Column("filed_date", sa.Date(), nullable=True),
        sa.Column("served_date", sa.Date(), nullable=True),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "client_contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
    )
    op.create_index("idx_domestic_cases_tenant_id", "domestic_cases", ["tenant_id"])
    op.execute("ALTER TABLE domestic_cases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE domestic_cases FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY domestic_cases_tenant_isolation ON domestic_cases
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """
    )

    # ── domestic_parties ──────────────────────────────────────────────────────
    op.create_table(
        "domestic_parties",
        _id_col(),
        _tenant_col(),
        _case_fk(),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("role", sa.String(50), server_default="respondent"),
        sa.Column(
            "is_client", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("gross_monthly_income", sa.Numeric(12, 2), nullable=True),
        sa.Column("federal_income_tax", sa.Numeric(12, 2), nullable=True),
        sa.Column("state_income_tax", sa.Numeric(12, 2), nullable=True),
        sa.Column("fica_tax", sa.Numeric(12, 2), nullable=True),
        sa.Column("required_retirement", sa.Numeric(12, 2), server_default="0"),
        sa.Column("union_dues", sa.Numeric(12, 2), server_default="0"),
        sa.Column("health_insurance_children", sa.Numeric(12, 2), server_default="0"),
        sa.Column("existing_support_paid", sa.Numeric(12, 2), server_default="0"),
        sa.Column("other_children_in_home", sa.Integer(), server_default="0"),
        sa.Column(
            "is_imputed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("imputed_basis", sa.String(100), nullable=True),
        sa.Column("annual_overnights", sa.Integer(), server_default="0"),
        sa.Column("email", sa.String(300), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )

    # ── domestic_children ─────────────────────────────────────────────────────
    op.create_table(
        "domestic_children",
        _id_col(),
        _tenant_col(),
        _case_fk(),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        _party_fk("primary_residence_party_id"),
        sa.Column(
            "has_special_needs",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )

    # ── custody_arrangements ──────────────────────────────────────────────────
    op.create_table(
        "custody_arrangements",
        _id_col(),
        _tenant_col(),
        _case_fk(),
        sa.Column("legal_custody", sa.String(50), server_default="joint"),
        sa.Column("physical_custody", sa.String(50), server_default="primary"),
        sa.Column("calc_custody_type", sa.String(20), server_default="primary"),
        _party_fk("primary_party_id"),
        sa.Column("children_with_party_a", sa.Integer(), server_default="0"),
        sa.Column("schedule_description", sa.Text(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )

    # ── child_support_calculations ────────────────────────────────────────────
    op.create_table(
        "child_support_calculations",
        _id_col(),
        _tenant_col(),
        _case_fk(),
        sa.Column("label", sa.String(300), nullable=True),
        sa.Column("jurisdiction", sa.String(2), server_default="ND"),
        sa.Column("model_type", sa.String(50), nullable=True),
        sa.Column("schedule_version", sa.String(50), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("num_children", sa.Integer(), server_default="0"),
        sa.Column("obligor_role", sa.String(50), nullable=True),
        sa.Column("presumptive_amount", sa.Numeric(12, 2), server_default="0"),
        sa.Column("final_amount", sa.Numeric(12, 2), server_default="0"),
        sa.Column("deviation_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("deviation_reason", sa.Text(), nullable=True),
        sa.Column("input_snapshot", JSONB(), nullable=False),
        sa.Column("worksheet", JSONB(), nullable=False),
        sa.Column(
            "is_final", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
    )

    # ── support_orders ────────────────────────────────────────────────────────
    op.create_table(
        "support_orders",
        _id_col(),
        _tenant_col(),
        _case_fk(),
        _party_fk("obligor_party_id"),
        _party_fk("obligee_party_id"),
        sa.Column(
            "calculation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("child_support_calculations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("monthly_amount", sa.Numeric(12, 2), server_default="0"),
        sa.Column("frequency", sa.String(20), server_default="monthly"),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("arrears_balance", sa.Numeric(12, 2), server_default="0"),
        sa.Column("status", sa.String(50), server_default="proposed"),
        sa.Column("order_type", sa.String(50), server_default="child_support"),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )

    # ── support_payments ──────────────────────────────────────────────────────
    op.create_table(
        "support_payments",
        _id_col(),
        _tenant_col(),
        _case_fk(),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("support_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), server_default="0"),
        sa.Column("applied_to_current", sa.Numeric(12, 2), server_default="0"),
        sa.Column("applied_to_arrears", sa.Numeric(12, 2), server_default="0"),
        sa.Column("method", sa.String(50), nullable=True),
        sa.Column("reference_number", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_index("idx_support_payments_order_id", "support_payments", ["order_id"])

    # ── domestic_deadlines ────────────────────────────────────────────────────
    op.create_table(
        "domestic_deadlines",
        _id_col(),
        _tenant_col(),
        _case_fk(),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("deadline_type", sa.String(50), server_default="other"),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column(
            "assigned_to",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )

    # ── domestic_events ───────────────────────────────────────────────────────
    op.create_table(
        "domestic_events",
        _id_col(),
        _tenant_col(),
        _case_fk(),
        sa.Column("event_type", sa.String(50), server_default="note"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # RLS on every sub-table.
    for table in _SUBTABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in _SUBTABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
    op.execute(
        "DROP POLICY IF EXISTS domestic_cases_tenant_isolation ON domestic_cases"
    )
    op.drop_table("domestic_cases")
