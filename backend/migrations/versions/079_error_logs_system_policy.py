"""Allow tenantless error-log rows through RLS.

Revision ID: 079_error_logs_system_policy
Revises: 078
Create Date: 2026-07-06
"""

from alembic import op


revision = "079_error_logs_system_policy"
down_revision = "078"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP POLICY IF EXISTS error_logs_tenant_isolation ON error_logs")
    op.execute(
        """
        CREATE POLICY error_logs_tenant_isolation ON error_logs
        USING (
            tenant_id IS NULL
            OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id IS NULL
            OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        """
    )


def downgrade():
    op.execute("DROP POLICY IF EXISTS error_logs_tenant_isolation ON error_logs")
    op.execute(
        """
        CREATE POLICY error_logs_tenant_isolation ON error_logs
        USING (
            tenant_id = current_setting('app.current_tenant_id', true)::uuid
        )
        WITH CHECK (
            tenant_id = current_setting('app.current_tenant_id', true)::uuid
        )
        """
    )
