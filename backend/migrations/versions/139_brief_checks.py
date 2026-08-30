"""Persist review-first Brief Check results and attorney audit decisions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "144_brief_checks"
down_revision = "143_operating_trust"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("brief_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_filename", sa.String(500), nullable=False), sa.Column("input_sha256", sa.String(64), nullable=False), sa.Column("input_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), server_default="needs_review", nullable=False), sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("tenant_id", "matter_id", "input_sha256", name="uq_brief_checks_input"))
    op.create_index("ix_brief_checks_tenant_matter_created", "brief_checks", ["tenant_id", "matter_id", "created_at"])
    op.create_table("brief_check_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False), sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("brief_check_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True), sa.Column("action", sa.String(50), nullable=False), sa.Column("item_id", sa.String(120), nullable=True), sa.Column("decision", sa.String(40), nullable=True), sa.Column("note", sa.Text(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.ForeignKeyConstraint(["brief_check_id"], ["brief_checks.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_brief_check_audits_check_created", "brief_check_audits", ["brief_check_id", "created_at"])
    for table in ("brief_checks", "brief_check_audits"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)")


def downgrade():
    op.execute("DROP POLICY IF EXISTS brief_check_audits_tenant_isolation ON brief_check_audits")
    op.execute("DROP POLICY IF EXISTS brief_checks_tenant_isolation ON brief_checks")
    op.drop_index("ix_brief_check_audits_check_created", table_name="brief_check_audits"); op.drop_table("brief_check_audits")
    op.drop_index("ix_brief_checks_tenant_matter_created", table_name="brief_checks"); op.drop_table("brief_checks")
