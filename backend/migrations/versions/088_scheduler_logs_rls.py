"""Tenant-isolate scheduler logs and hide legacy unscoped rows.

Revision ID: 088_scheduler_logs_rls
Revises: 087_mcp_product_security
"""

from alembic import op

revision = "088_scheduler_logs_rls"
down_revision = "087_mcp_product_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_scheduler_logs_tenant_agent_run",
        "scheduler_logs",
        ["tenant_id", "agent_name", "run_at"],
    )
    op.execute("ALTER TABLE scheduler_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE scheduler_logs FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS scheduler_logs_tenant_isolation ON scheduler_logs"
    )
    op.execute(
        """
        CREATE POLICY scheduler_logs_tenant_isolation ON scheduler_logs
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS scheduler_logs_tenant_isolation ON scheduler_logs"
    )
    op.execute("ALTER TABLE scheduler_logs NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE scheduler_logs DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_scheduler_logs_tenant_agent_run", table_name="scheduler_logs")
