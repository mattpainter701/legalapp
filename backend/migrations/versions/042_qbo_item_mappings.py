"""Create qbo_item_mappings table for billing category → QBO Item mapping.

Revision ID: 042
Revises: 041
Create Date: 2026-06-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qbo_item_mappings",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_type",
            sa.String(50),
            nullable=False,
            comment="time_entry | expense | flat_fee | adjustment",
        ),
        sa.Column(
            "expense_category",
            sa.String(100),
            nullable=True,
            comment="Sub-type for expense rows (filing_fee, travel, etc.)",
        ),
        sa.Column("qbo_item_id", sa.String(100), nullable=False, comment="QBO Item.Id"),
        sa.Column("qbo_item_name", sa.String(200), nullable=False),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_type",
            "expense_category",
            name="uq_qbo_item_mappings_tenant_type_category",
        ),
    )
    op.create_index(
        "idx_qbo_item_mappings_tenant_id", "qbo_item_mappings", ["tenant_id"]
    )

    op.execute("ALTER TABLE qbo_item_mappings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE qbo_item_mappings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_qbo_item_mappings ON qbo_item_mappings
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_qbo_item_mappings ON qbo_item_mappings"
    )
    op.execute("ALTER TABLE qbo_item_mappings DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_qbo_item_mappings_tenant_id", table_name="qbo_item_mappings")
    op.drop_table("qbo_item_mappings")
