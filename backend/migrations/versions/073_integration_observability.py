"""073 - integration observability spine

Revision ID: 073_integration_observability
Revises: 072_cred_unique_constraints
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "073_integration_observability"
down_revision = "072_cred_unique_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("tenant_credentials", "user_oauth_tokens"):
        op.add_column(table, sa.Column("missing_scopes", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "scopes_version", sa.Integer(), server_default="1", nullable=False
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "health",
                sa.String(length=30),
                server_default="healthy",
                nullable=False,
            ),
        )
        op.add_column(
            table,
            sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(table, sa.Column("last_refresh_error", sa.Text(), nullable=True))

    op.create_table(
        "integration_sync_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("items_ok", sa.Integer(), server_default="0", nullable=False),
        sa.Column("items_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_integration_sync_runs_tenant_provider_job",
        "integration_sync_runs",
        ["tenant_id", "provider", "job_type", "started_at"],
    )
    op.create_index(
        "idx_integration_sync_runs_status", "integration_sync_runs", ["status"]
    )
    op.execute("ALTER TABLE integration_sync_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE integration_sync_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY integration_sync_runs_tenant_isolation
        ON integration_sync_runs
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY integration_sync_runs_rls_bypass
        ON integration_sync_runs
        USING (current_setting('app.rls_bypass', true) = 'on')
        WITH CHECK (current_setting('app.rls_bypass', true) = 'on')
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS integration_sync_runs_rls_bypass ON integration_sync_runs"
    )
    op.execute(
        "DROP POLICY IF EXISTS integration_sync_runs_tenant_isolation ON integration_sync_runs"
    )
    op.drop_index(
        "idx_integration_sync_runs_status", table_name="integration_sync_runs"
    )
    op.drop_index(
        "idx_integration_sync_runs_tenant_provider_job",
        table_name="integration_sync_runs",
    )
    op.drop_table("integration_sync_runs")

    for table in ("user_oauth_tokens", "tenant_credentials"):
        op.drop_column(table, "last_refresh_error")
        op.drop_column(table, "last_refresh_at")
        op.drop_column(table, "health")
        op.drop_column(table, "scopes_version")
        op.drop_column(table, "missing_scopes")
