"""Create trust_accounts and trust_transactions tables for IOLTA trust accounting.

Revision ID: 017
Revises: 016
Create Date: 2026-06-02 00:00:02.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- trust_accounts ---
    op.create_table(
        "trust_accounts",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("account_name", sa.String(300), nullable=False),
        sa.Column("bank_name", sa.String(200), nullable=True),
        sa.Column(
            "account_number_masked",
            sa.String(10),
            nullable=True,
            comment="Last 4 digits only",
        ),
        sa.Column(
            "current_balance",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
            comment="Running balance from posted trust transactions",
        ),
        sa.Column(
            "minimum_balance",
            sa.Numeric(12, 2),
            nullable=True,
            comment="Minimum threshold for evergreen retainer auto-replenishment",
        ),
        sa.Column(
            "auto_replenish_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "auto_replenish_amount",
            sa.Numeric(12, 2),
            nullable=True,
            comment="Amount to replenish when balance drops below minimum",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="RESTRICT"),
    )
    op.create_index("idx_trust_accounts_tenant_id", "trust_accounts", ["tenant_id"])
    op.create_index(
        "uq_trust_accounts_tenant_matter",
        "trust_accounts",
        ["tenant_id", "matter_id"],
        unique=True,
    )

    # --- trust_transactions ---
    op.create_table(
        "trust_transactions",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("trust_account_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "transaction_type",
            sa.String(50),
            nullable=False,
            comment="deposit, disbursement, transfer_in, transfer_out, replenishment, fee, adjustment",
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "transaction_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column("reference_number", sa.String(200), nullable=True),
        sa.Column("check_number", sa.String(50), nullable=True),
        sa.Column(
            "is_reconciled", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["trust_account_id"], ["trust_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "idx_trust_transactions_account_id", "trust_transactions", ["trust_account_id"]
    )
    op.create_index(
        "idx_trust_transactions_tenant_id", "trust_transactions", ["tenant_id"]
    )

    # --- RLS policies ---
    for table in ("trust_accounts", "trust_transactions"):
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
    for table in ("trust_accounts", "trust_transactions"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("idx_trust_transactions_tenant_id", table_name="trust_transactions")
    op.drop_index("idx_trust_transactions_account_id", table_name="trust_transactions")
    op.drop_table("trust_transactions")

    op.drop_index("uq_trust_accounts_tenant_matter", table_name="trust_accounts")
    op.drop_index("idx_trust_accounts_tenant_id", table_name="trust_accounts")
    op.drop_table("trust_accounts")
