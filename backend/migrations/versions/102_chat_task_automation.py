"""Add assistant-proposed task actions and exactly-once automation runs.

The assistant may draft follow-through work (for example a client email
requesting missing documents) and place it on the work board in Review. The
drafted payload lives on ``tasks.pending_action``; approval is an ordinary human
transition, and execution is a deterministic hook with no model in the path.

``task_automation_runs`` is what keeps that execution exactly-once. The unique
constraint on ``(task_id, idempotency_key)`` means a double-clicked Approve or
two concurrent transitions collide on the insert instead of sending a client two
copies of the same email.

Additive and reversible: the new column is nullable and the new flag defaults
false, so existing tenants see no behavior change until an operator opts them in.

Revision ID: 102_chat_task_automation
Revises: 101_doc_revisions
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "102_chat_task_automation"
down_revision = "101_doc_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("pending_action", sa.JSON(), nullable=True),
    )
    # Cards the assistant showed for this turn. The tasks are the source of
    # truth; this is the rendering record, so reloading a conversation shows the
    # same proposals instead of losing them.
    op.add_column(
        "messages",
        sa.Column("proposed_actions", sa.JSON(), nullable=True),
    )
    # Off for every existing tenant. Chat actions are opted into per tenant.
    op.add_column(
        "tenant_settings",
        sa.Column(
            "enable_chat_actions",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "task_automation_runs",
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
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        # queued -> sending -> sent | failed
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "triggered_by_user_id",
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "task_id", "idempotency_key", name="uq_task_automation_runs_task_key"
        ),
    )
    op.create_index(
        "idx_task_automation_runs_tenant_status",
        "task_automation_runs",
        ["tenant_id", "status", "created_at"],
    )

    # Tenant isolation is enforced by RLS on every tenant-scoped table, so a new
    # table must opt in explicitly or it would be readable across tenants.
    # NULLIF matches the existing policies (see 100_task_work_board): an unset or
    # empty app.current_tenant_id becomes NULL so the predicate is false, rather
    # than raising on ''::uuid. Absence must deny, not error.
    op.execute("ALTER TABLE task_automation_runs ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY task_automation_runs_tenant_isolation ON task_automation_runs
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        """
    )
    op.execute("ALTER TABLE task_automation_runs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS task_automation_runs_tenant_isolation "
        "ON task_automation_runs"
    )
    op.drop_index(
        "idx_task_automation_runs_tenant_status", table_name="task_automation_runs"
    )
    op.drop_table("task_automation_runs")
    op.drop_column("tenant_settings", "enable_chat_actions")
    op.drop_column("messages", "proposed_actions")
    op.drop_column("tasks", "pending_action")
