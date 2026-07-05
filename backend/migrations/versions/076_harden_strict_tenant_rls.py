"""Harden strict tenant RLS policies for missing tenant GUCs.

Revision ID: 076_harden_strict_tenant_rls
Revises: 075_billing_timer_and_qbo_dedupe
Create Date: 2026-07-05
"""

from alembic import op

revision = "076_harden_strict_tenant_rls"
down_revision = "075_billing_timer_and_qbo_dedupe"
branch_labels = None
depends_on = None


POLICIES = (
    ("contacts", "contacts_tenant_isolation"),
    ("tasks", "tasks_tenant_isolation"),
    ("communication_logs", "commlogs_tenant_isolation"),
    ("leads", "leads_tenant_isolation"),
)


def upgrade():
    for table, policy in POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy} ON {table}
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            )
            """
        )


def downgrade():
    for table, policy in POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy} ON {table}
            USING (tenant_id = current_setting('app.tenant_id')::uuid)
            """
        )
