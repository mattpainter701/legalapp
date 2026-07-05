"""Billing overhaul: live timers, QBO TimeActivity dedupe, invoice sent_at.

- time_entries.timer_started_at: set while a live timer is running
- time_entries.qbo_timeactivity_id: QBO TimeActivity.Id once synced, so
  repeated sync runs no longer create duplicate TimeActivities in QBO
- invoices.sent_at: audit timestamp for the draft -> sent transition
- partial unique index enforcing at most one running timer per user
"""

import sqlalchemy as sa
from alembic import op

revision = "075_billing_timer_and_qbo_dedupe"
down_revision = "074_task_closure_tracking"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "time_entries",
        sa.Column("timer_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "time_entries",
        sa.Column("qbo_timeactivity_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_time_entries_running_timer",
        "time_entries",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("timer_started_at IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_time_entries_running_timer", table_name="time_entries")
    op.drop_column("invoices", "sent_at")
    op.drop_column("time_entries", "qbo_timeactivity_id")
    op.drop_column("time_entries", "timer_started_at")
