"""Persist reviewable plugin skill work products.

Revision ID: 098_plugin_skill_runs
Revises: 097_mediation_work_queue
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "098_plugin_skill_runs"
down_revision = "097_mediation_work_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_skill_runs",
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
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
        ),
        sa.Column("plugin_name", sa.String(100), nullable=False),
        sa.Column("skill_name", sa.String(100), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("memo", sa.Text(), nullable=False),
        sa.Column(
            "findings", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.Column(
            "gates_triggered",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.Column("model_used", sa.String(255), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "requires_attorney_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "review_status", sa.String(30), nullable=False, server_default="draft"
        ),
        sa.Column(
            "reviewed_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "review_status IN ('draft', 'approved', 'rejected')",
            name="ck_plugin_skill_runs_review_status",
        ),
    )
    op.create_index(
        "idx_plugin_skill_runs_tenant_created",
        "plugin_skill_runs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "idx_plugin_skill_runs_matter_created",
        "plugin_skill_runs",
        ["matter_id", "created_at"],
    )
    op.execute("ALTER TABLE plugin_skill_runs ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY plugin_skill_runs_tenant_isolation ON plugin_skill_runs
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE plugin_skill_runs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index(
        "idx_plugin_skill_runs_matter_created", table_name="plugin_skill_runs"
    )
    op.drop_index(
        "idx_plugin_skill_runs_tenant_created", table_name="plugin_skill_runs"
    )
    op.drop_table("plugin_skill_runs")
