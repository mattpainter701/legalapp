"""054 — Trust pooled ledger: bank accounts & reconciliation snapshots (Task 1303).

Deepens the existing per-matter trust accounting model into a real pooled
IOLTA model:

  - trust_bank_accounts (RLS by tenant): a real-world pooled bank account.
    Many client-ledger ``trust_accounts`` rows can map to one pooled bank
    account via the new nullable ``trust_accounts.bank_account_id`` FK.
  - trust_reconciliations (RLS by tenant): persisted three-way reconciliation
    snapshots, either for a pooled bank account (``bank_account_id`` set) or
    for a single per-matter trust account (``trust_account_id`` set, the
    existing ``POST /accounts/{id}/reconcile`` flow).

Both new FKs on trust_accounts/trust_reconciliations are nullable so existing
per-matter trust accounts keep working unchanged.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── trust_bank_accounts ─────────────────────────────────────────────
    op.create_table(
        "trust_bank_accounts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("account_name", sa.String(300), nullable=False),
        sa.Column("bank_name", sa.String(200), nullable=True),
        sa.Column("account_number_masked", sa.String(10), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_trust_bank_accounts_tenant_id",
        "trust_bank_accounts",
        ["tenant_id"],
    )

    op.execute("ALTER TABLE trust_bank_accounts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trust_bank_accounts FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_trust_bank_accounts ON trust_bank_accounts
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    # ── trust_accounts.bank_account_id ──────────────────────────────────
    op.add_column(
        "trust_accounts",
        sa.Column("bank_account_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_trust_accounts_bank_account_id",
        "trust_accounts",
        "trust_bank_accounts",
        ["bank_account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_trust_accounts_bank_account_id",
        "trust_accounts",
        ["bank_account_id"],
    )

    # ── trust_reconciliations ────────────────────────────────────────────
    op.create_table(
        "trust_reconciliations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("bank_account_id", UUID(as_uuid=True), nullable=True),
        sa.Column("trust_account_id", UUID(as_uuid=True), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("bank_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("book_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("trust_liability", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "unallocated",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "outstanding_deposits",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "outstanding_disbursements",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("adjusted_bank_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("difference", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_reconciled", sa.Boolean(), nullable=False),
        sa.Column("reconciling_items", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reconciled_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["bank_account_id"], ["trust_bank_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["trust_account_id"], ["trust_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["reconciled_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_trust_reconciliations_tenant_id",
        "trust_reconciliations",
        ["tenant_id"],
    )
    op.create_index(
        "idx_trust_reconciliations_bank_account_id",
        "trust_reconciliations",
        ["bank_account_id"],
    )

    op.execute("ALTER TABLE trust_reconciliations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trust_reconciliations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_trust_reconciliations ON trust_reconciliations
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_trust_reconciliations "
        "ON trust_reconciliations"
    )
    op.execute("ALTER TABLE trust_reconciliations DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "idx_trust_reconciliations_bank_account_id",
        table_name="trust_reconciliations",
    )
    op.drop_index(
        "idx_trust_reconciliations_tenant_id", table_name="trust_reconciliations"
    )
    op.drop_table("trust_reconciliations")

    op.drop_index("idx_trust_accounts_bank_account_id", table_name="trust_accounts")
    op.drop_constraint(
        "fk_trust_accounts_bank_account_id", "trust_accounts", type_="foreignkey"
    )
    op.drop_column("trust_accounts", "bank_account_id")

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_trust_bank_accounts "
        "ON trust_bank_accounts"
    )
    op.execute("ALTER TABLE trust_bank_accounts DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_trust_bank_accounts_tenant_id", table_name="trust_bank_accounts")
    op.drop_table("trust_bank_accounts")
