"""Create user_memories table for storing learned user preferences and context.

Revision ID: 011
Revises: 010
Create Date: 2026-06-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_memories",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_type", sa.String(50), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_user_memory_key",
        "user_memories",
        ["user_id", "memory_type", "key"],
        unique=True,
    )
    op.create_index(
        "idx_user_memory_user_id",
        "user_memories",
        ["user_id"],
    )
    op.create_index(
        "idx_user_memory_type",
        "user_memories",
        ["memory_type"],
    )

    op.execute("ALTER TABLE user_memories ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_memories FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_user_memories ON user_memories
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_user_memories ON user_memories")
    op.execute("ALTER TABLE user_memories DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_user_memory_type", table_name="user_memories")
    op.drop_index("idx_user_memory_user_id", table_name="user_memories")
    op.drop_index("uq_user_memory_key", table_name="user_memories")
    op.drop_table("user_memories")
