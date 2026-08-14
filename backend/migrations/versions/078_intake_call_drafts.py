"""Add intake call-drafts persistence for autosave durability.

Revision ID: 078
Revises: 077_error_logs_nullable_tenant
Create Date: 2026-07-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "078"
down_revision: Union[str, None] = "077_error_logs_nullable_tenant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "intake_call_drafts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", JSONB, nullable=False),
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
    )
    op.create_index("idx_intake_call_drafts_tenant", "intake_call_drafts", ["tenant_id"])
    op.create_index(
        "idx_intake_call_drafts_created_by",
        "intake_call_drafts",
        ["tenant_id", "created_by"],
    )
    _enable_rls("intake_call_drafts")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS intake_call_drafts_tenant_isolation ON intake_call_drafts")
    op.drop_index("idx_intake_call_drafts_created_by", table_name="intake_call_drafts")
    op.drop_index("idx_intake_call_drafts_tenant", table_name="intake_call_drafts")
    op.drop_table("intake_call_drafts")
