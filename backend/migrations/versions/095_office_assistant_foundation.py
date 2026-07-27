"""Add immutable Entra links and metadata-only Office action audit.

Revision ID: 095_office_assistant
Revises: 094_admin_conf_call_content
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "095_office_assistant"
down_revision = "094_admin_conf_call_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("entra_tenant_id", sa.String(36), nullable=True))
    op.add_column("users", sa.Column("entra_object_id", sa.String(36), nullable=True))
    op.create_unique_constraint(
        "uq_users_entra_identity",
        "users",
        ["entra_tenant_id", "entra_object_id"],
    )

    op.create_table(
        "office_action_runs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_id", sa.String(100), nullable=False),
        sa.Column("surface", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("action_types", sa.JSON(), nullable=False),
        sa.Column("action_count", sa.Integer(), nullable=False),
        sa.Column("result_action_count", sa.Integer(), nullable=True),
        sa.Column("context_size", sa.Integer(), nullable=False),
        sa.Column("base_fingerprint_hmac_sha256", sa.String(64), nullable=False),
        sa.Column("result_fingerprint_hmac_sha256", sa.String(64), nullable=True),
        sa.Column("instruction_hmac_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("model_alias", sa.String(200), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "surface IN ('word', 'excel', 'outlook')",
            name="ck_office_action_runs_surface",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'applied', 'rejected', 'stale', 'failed')",
            name="ck_office_action_runs_status",
        ),
        sa.CheckConstraint(
            "action_count >= 0 AND context_size >= 0 AND "
            "(result_action_count IS NULL OR result_action_count >= 0)",
            name="ck_office_action_runs_counts",
        ),
    )
    op.create_index(
        "idx_office_action_runs_tenant_created",
        "office_action_runs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "idx_office_action_runs_user_created",
        "office_action_runs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "idx_office_action_runs_plan",
        "office_action_runs",
        ["tenant_id", "plan_id"],
        unique=True,
    )
    op.execute("ALTER TABLE office_action_runs ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY office_action_runs_tenant_isolation
        ON office_action_runs
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )
    op.execute("ALTER TABLE office_action_runs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("idx_office_action_runs_plan", table_name="office_action_runs")
    op.drop_index(
        "idx_office_action_runs_user_created", table_name="office_action_runs"
    )
    op.drop_index(
        "idx_office_action_runs_tenant_created", table_name="office_action_runs"
    )
    op.drop_table("office_action_runs")
    op.drop_constraint("uq_users_entra_identity", "users", type_="unique")
    op.drop_column("users", "entra_object_id")
    op.drop_column("users", "entra_tenant_id")
