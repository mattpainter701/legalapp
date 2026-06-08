"""Force RLS on six lagging tables and add the auth cross-tenant bypass policy.

Revision ID: 044
Revises: 043
Create Date: 2026-06-05 00:00:00.000000

Security rationale
------------------
The application is moving from connecting to Postgres as a SUPERUSER (which
bypasses Row Level Security entirely) to a least-privilege role that is neither
superuser nor table owner (see scripts/provision_app_role.sql). Under such a
role, RLS is only enforced when policies exist AND the table both ENABLEs and
FORCEs row level security:

* ``ENABLE ROW LEVEL SECURITY`` turns policies on for ordinary roles, but the
  table OWNER still bypasses them.
* ``FORCE ROW LEVEL SECURITY`` makes policies apply to the owner too.

Six tables were ENABLE'd but never FORCE'd in earlier migrations
(communication_logs 020, contacts 018, leads 020, matter_documents 022,
matter_parties 021, tasks 019). Once the runtime connects as a non-superuser
that happens to also be (or share privileges with) the owner, ENABLE-without-
FORCE means RLS is silently bypassed. This migration adds FORCE to close that
gap so tenant isolation is structurally enforced for all six.

It also adds a narrowly-scoped PERMISSIVE bypass policy on ``users`` keyed on
the transaction-local ``app.rls_bypass`` GUC. The auth router performs
legitimate CROSS-TENANT lookups (login / forgot / reset by email, OAuth
exchange by id) that have no tenant context. Postgres ORs multiple PERMISSIVE
policies, so this policy coexists with ``tenant_isolation_users`` and only
opens up access when the auth path has explicitly set ``app.rls_bypass = 'on'``
(via app.database.enable_rls_bypass). All other requests leave the GUC unset,
so the bypass evaluates false and normal tenant isolation applies.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that were ENABLE'd but not FORCE'd in earlier migrations.
_FORCE_TABLES = [
    "communication_logs",
    "contacts",
    "leads",
    "matter_documents",
    "matter_parties",
    "tasks",
]


def upgrade() -> None:
    # FORCE RLS so the policies apply even to the table owner. These tables are
    # all created and RLS-ENABLE'd in migrations 018-022, so they exist here.
    for table in _FORCE_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # Auth cross-tenant bypass on users. PERMISSIVE policies are OR'd, so this
    # sits alongside tenant_isolation_users and only grants access when the auth
    # path has set app.rls_bypass = 'on' for the current transaction.
    op.execute("DROP POLICY IF EXISTS rls_bypass_users ON users")
    op.execute(
        """
        CREATE POLICY rls_bypass_users ON users
        FOR ALL TO PUBLIC
        USING (current_setting('app.rls_bypass', true) = 'on')
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS rls_bypass_users ON users")
    for table in _FORCE_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
