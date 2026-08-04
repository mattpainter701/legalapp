"""Add the legal work board workflow and append-only task history.

Revision ID: 100_task_work_board
Revises: 099_chat_latency_breakdown
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "100_task_work_board"
down_revision = "099_chat_latency_breakdown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_settings",
        sa.Column(
            "enable_task_board",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )
    op.execute(
        "UPDATE tasks SET status_changed_at = COALESCE(updated_at, created_at, now()) "
        "WHERE status_changed_at IS NULL"
    )
    op.execute(
        "UPDATE tasks SET completed_at = COALESCE(updated_at, created_at, now()) "
        "WHERE status = 'completed' AND completed_at IS NULL"
    )
    op.alter_column("tasks", "status_changed_at", nullable=False)
    op.add_column("tasks", sa.Column("waiting_reason", sa.Text(), nullable=True))
    op.add_column(
        "tasks", sa.Column("waiting_follow_up_date", sa.Date(), nullable=True)
    )
    op.add_column(
        "tasks",
        sa.Column(
            "reviewer_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "idx_tasks_tenant_status_assignee",
        "tasks",
        ["tenant_id", "status", "assigned_to_user_id"],
    )
    op.create_index(
        "idx_tasks_tenant_status_due",
        "tasks",
        ["tenant_id", "status", "due_date"],
    )

    op.create_table(
        "task_events",
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
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("from_status", sa.String(50), nullable=True),
        sa.Column("to_status", sa.String(50), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_task_events_tenant_task_created",
        "task_events",
        ["tenant_id", "task_id", "created_at"],
    )
    op.create_index(
        "idx_task_events_tenant_type_created",
        "task_events",
        ["tenant_id", "event_type", "created_at"],
    )
    op.execute("ALTER TABLE task_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY task_events_tenant_isolation ON task_events
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        """
    )
    op.execute("ALTER TABLE task_events FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("task_events")
    op.drop_index("idx_tasks_tenant_status_due", table_name="tasks")
    op.drop_index("idx_tasks_tenant_status_assignee", table_name="tasks")
    op.drop_column("tasks", "version")
    op.drop_column("tasks", "reviewer_user_id")
    op.drop_column("tasks", "waiting_follow_up_date")
    op.drop_column("tasks", "waiting_reason")
    op.drop_column("tasks", "status_changed_at")
    op.drop_column("tenant_settings", "enable_task_board")
