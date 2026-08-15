"""Persist immutable approved-action and provider delivery evidence.

Revision ID: 103_task_action_delivery_audit
Revises: 102_chat_task_automation

The task payload is cleared after a confirmed send so it cannot execute again.
These additive fields retain the exact approved recipients, subject, body, and
source chips on the automation run, together with its canonical digest and any
provider/message identifiers returned by the connected mailbox.
"""

from alembic import op
import sqlalchemy as sa


revision = "103_task_action_delivery_audit"
down_revision = "102_chat_task_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "rag_corpus_revision",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("sync_source_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_documents_tenant_sync_source",
        "documents",
        ["tenant_id", "sync_source_key"],
    )
    op.add_column(
        "task_automation_runs",
        sa.Column("action_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "task_automation_runs",
        sa.Column("action_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "task_automation_runs",
        sa.Column("provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "task_automation_runs",
        sa.Column("provider_message_id", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "task_automation_runs",
        sa.Column("delivery_detail", sa.Text(), nullable=True),
    )
    op.add_column(
        "task_automation_runs",
        sa.Column("delivery_certainty", sa.String(length=30), nullable=True),
    )
    # Pre-103 jobs do not carry the immutable approval/run key or snapshot.
    # Never let a new worker infer one from mutable task state. Conservatively
    # terminalize both queued and sending attempts; attorneys can inspect Sent
    # Items and explicitly reapprove under the new protocol.
    legacy_detail = (
        "Delivery not confirmed during the action-audit upgrade. No automatic "
        "retry was attempted; check Sent Items before explicit reapproval."
    )
    # These are cross-tenant cutover updates on FORCE-RLS tables. Alembic runs
    # under a non-customer maintenance context, so enter an owner-only window
    # transactionally and restore FORCE immediately afterward.
    op.execute("ALTER TABLE task_automation_runs NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE durable_jobs NO FORCE ROW LEVEL SECURITY")
    op.get_bind().execute(
        sa.text(
            """
            UPDATE task_automation_runs
               SET status = 'failed',
                   error_message = :detail,
                   delivery_detail = :detail,
                   delivery_certainty = 'outcome_unknown',
                   completed_at = COALESCE(completed_at, now())
             WHERE status IN ('queued', 'sending')
            """
        ),
        {"detail": legacy_detail},
    )
    op.get_bind().execute(
        sa.text(
            """
            UPDATE durable_jobs
               SET status = 'completed',
                   progress = 100,
                   result = json_build_object(
                       'delivery', 'legacy_outcome_unknown',
                       'cutover', true
                   ),
                   completed_at = COALESCE(completed_at, now()),
                   leased_at = NULL,
                   lease_owner = NULL
             WHERE kind = 'task_automation'
               AND status IN ('pending', 'running')
               AND (payload::jsonb ->> 'approval_idempotency_key') IS NULL
            """
        )
    )
    op.execute("ALTER TABLE durable_jobs FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE task_automation_runs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column("task_automation_runs", "delivery_certainty")
    op.drop_column("task_automation_runs", "delivery_detail")
    op.drop_column("task_automation_runs", "provider_message_id")
    op.drop_column("task_automation_runs", "provider")
    op.drop_column("task_automation_runs", "action_sha256")
    op.drop_column("task_automation_runs", "action_snapshot")
    op.drop_index("ix_documents_tenant_sync_source", table_name="documents")
    op.drop_column("documents", "sync_source_key")
    op.drop_column("tenants", "rag_corpus_revision")
