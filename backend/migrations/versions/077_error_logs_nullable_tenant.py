"""077 — Allow tenant-less/system errors in error_logs.

Revision ID: 077_error_logs_nullable_tenant
Revises: 076_harden_strict_tenant_rls
Create Date: 2026-07-05
"""

from alembic import op


revision = "077_error_logs_nullable_tenant"
down_revision = "076_harden_strict_tenant_rls"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("error_logs", "tenant_id", nullable=True)


def downgrade():
    op.execute("DELETE FROM error_logs WHERE tenant_id IS NULL")
    op.alter_column("error_logs", "tenant_id", nullable=False)
