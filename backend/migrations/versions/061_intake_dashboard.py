"""Add local intake dashboard archive and partner rotation.

Revision ID: 061
Revises: 060
Create Date: 2026-06-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "061"
down_revision: Union[str, None] = "060"
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
            tenant_id = current_setting('app.current_tenant_id', true)::uuid
        )
        WITH CHECK (
            tenant_id = current_setting('app.current_tenant_id', true)::uuid
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "legacy_call_records",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_system",
            sa.String(100),
            nullable=False,
            server_default="legacy_csv",
        ),
        sa.Column("source_row_id", sa.String(200), nullable=False),
        sa.Column("caller_name", sa.String(500), nullable=True),
        sa.Column("caller_phone", sa.String(100), nullable=True),
        sa.Column("normalized_phone", sa.String(32), nullable=True),
        sa.Column("practice_area", sa.String(100), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("prior_attorney_name", sa.String(255), nullable=True),
        sa.Column("call_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("raw_payload", JSONB(), nullable=True),
        sa.Column(
            "imported_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_row_id",
            name="uq_legacy_call_records_source",
        ),
    )
    op.create_index("idx_legacy_call_records_tenant", "legacy_call_records", ["tenant_id"])
    op.create_index(
        "idx_legacy_call_records_phone",
        "legacy_call_records",
        ["tenant_id", "normalized_phone"],
    )
    op.create_index(
        "idx_legacy_call_records_name",
        "legacy_call_records",
        ["tenant_id", "caller_name"],
    )
    op.create_index(
        "idx_legacy_call_records_call_date",
        "legacy_call_records",
        ["tenant_id", "call_date"],
    )
    _enable_rls("legacy_call_records")

    op.create_table(
        "partner_rotation_state",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("practice_area", sa.String(100), nullable=False),
        sa.Column("eligible_user_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "last_assigned_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        sa.UniqueConstraint(
            "tenant_id",
            "practice_area",
            name="uq_partner_rotation_state_tenant_practice",
        ),
    )
    op.create_index(
        "idx_partner_rotation_state_tenant",
        "partner_rotation_state",
        ["tenant_id"],
    )
    _enable_rls("partner_rotation_state")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS partner_rotation_state_tenant_isolation ON partner_rotation_state")
    op.drop_index("idx_partner_rotation_state_tenant", table_name="partner_rotation_state")
    op.drop_table("partner_rotation_state")

    op.execute("DROP POLICY IF EXISTS legacy_call_records_tenant_isolation ON legacy_call_records")
    op.drop_index("idx_legacy_call_records_call_date", table_name="legacy_call_records")
    op.drop_index("idx_legacy_call_records_name", table_name="legacy_call_records")
    op.drop_index("idx_legacy_call_records_phone", table_name="legacy_call_records")
    op.drop_index("idx_legacy_call_records_tenant", table_name="legacy_call_records")
    op.drop_table("legacy_call_records")
