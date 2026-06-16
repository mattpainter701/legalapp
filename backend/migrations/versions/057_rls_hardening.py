"""RLS hardening: add policies to error_logs + api_access_logs; FORCE estate tables.

Revision ID: 057
Revises: 056
Create Date: 2026-06-16

Security rationale
------------------
Three categories of gap identified in the security review:

1. ``error_logs`` (migration 041) and ``api_access_logs`` (migration 039) were
   created with a tenant_id column but no RLS policy. Today both tables are only
   read via code that adds an explicit tenant_id filter, so there is no live leak,
   but a missing policy means any future query without that filter crosses tenant
   boundaries silently. This migration adds ENABLE + FORCE + policy to both.

2. Seven estate detail tables (migration 030 ``_enable_rls`` helper) were
   ENABLE'd with a policy but never FORCE'd. Under the least-privilege
   ``clarity_app`` role (non-owner, NOBYPASSRLS) ENABLE alone is sufficient, but
   FORCE is belt-and-suspenders: it makes policies apply even if the runtime role
   ever regains ownership (e.g. after a role mis-config). Mirror what migration
   044 did for the six other tables it remediated.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "057"
down_revision: Union[str, None] = "056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_POLICY_TABLES = [
    "error_logs",
    "api_access_logs",
]

_FORCE_ONLY_TABLES = [
    "estate_fiduciaries",
    "estate_beneficiaries",
    "estate_assets",
    "estate_liabilities",
    "estate_distributions",
    "estate_deadlines",
    "estate_accounting_entries",
]


def upgrade() -> None:
    # 1. Add full RLS to tables that have tenant_id but no policy at all.
    for table in _POLICY_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
            )
            WITH CHECK (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
            )
            """
        )

    # 2. FORCE the seven estate detail tables that were ENABLE'd but not FORCE'd.
    for table in _FORCE_ONLY_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in _FORCE_ONLY_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    for table in _POLICY_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
