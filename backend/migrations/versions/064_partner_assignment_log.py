"""Add partner assignment log.

Revision ID: 064
Revises: 063
Create Date: 2026-06-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "partner_assignment_log",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=True),
        sa.Column("contact_id", UUID(as_uuid=True), nullable=True),
        sa.Column("communication_id", UUID(as_uuid=True), nullable=True),
        sa.Column("practice_area", sa.String(100), nullable=True),
        sa.Column(
            "assigned_to_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assigned_to_name", sa.String(255), nullable=True),
        sa.Column("rotation_rule_id", UUID(as_uuid=True), nullable=True),
        sa.Column("assignment_method", sa.String(50), nullable=False),
        sa.Column(
            "assigned_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assigned_by_name", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_partner_assignment_log_tenant", "partner_assignment_log", ["tenant_id"]
    )
    op.create_index(
        "idx_partner_assignment_log_created",
        "partner_assignment_log",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "idx_partner_assignment_log_assignee",
        "partner_assignment_log",
        ["tenant_id", "assigned_to_user_id"],
    )
    _enable_rls("partner_assignment_log")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS partner_assignment_log_tenant_isolation ON partner_assignment_log"
    )
    op.drop_index(
        "idx_partner_assignment_log_assignee", table_name="partner_assignment_log"
    )
    op.drop_index(
        "idx_partner_assignment_log_created", table_name="partner_assignment_log"
    )
    op.drop_index(
        "idx_partner_assignment_log_tenant", table_name="partner_assignment_log"
    )
    op.drop_table("partner_assignment_log")
