"""019 — Create tasks table.

Revision ID: 019
Revises: 018
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "task_type", sa.String(50), nullable=False, server_default="general"
        ),
        sa.Column(
            "status", sa.String(50), nullable=False, server_default="pending"
        ),
        sa.Column(
            "priority", sa.String(20), nullable=False, server_default="medium"
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("due_time", sa.Time(), nullable=True),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "assigned_to_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source", sa.String(50), nullable=False, server_default="manual"
        ),
        sa.Column("external_ref", sa.String(500), nullable=True),
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

    op.create_index("idx_tasks_tenant_id", "tasks", ["tenant_id"])
    op.create_index("idx_tasks_matter_id", "tasks", ["matter_id"])
    op.create_index(
        "idx_tasks_assigned_to", "tasks", ["tenant_id", "assigned_to_user_id"]
    )
    op.create_index("idx_tasks_due_date", "tasks", ["tenant_id", "due_date"])
    op.create_index("idx_tasks_status", "tasks", ["tenant_id", "status"])

    op.execute("ALTER TABLE tasks ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tasks_tenant_isolation ON tasks
        USING (tenant_id = current_setting('app.tenant_id')::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tasks_tenant_isolation ON tasks")
    op.drop_table("tasks")
