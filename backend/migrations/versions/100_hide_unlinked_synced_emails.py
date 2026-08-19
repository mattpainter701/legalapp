"""Hide historical mailbox-sync records that were never linked to a matter.

Revision ID: 100_hide_unlinked_synced_emails
Revises: 099_chat_latency_breakdown
"""

from alembic import op


revision = "100_hide_unlinked_synced_emails"
down_revision = "099_chat_latency_breakdown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # These rows came from the mailbox syncer's provider message IDs, but have
    # no established matter/contact relationship. Retain the rows for audit
    # while removing them from the Communications workspace.
    op.execute(
        """
        UPDATE communication_logs
           SET status = 'deleted', updated_at = now()
         WHERE channel = 'email'
           AND direction = 'inbound'
           AND status <> 'deleted'
           AND matter_id IS NULL
           AND contact_id IS NULL
           AND (
             external_ref LIKE 'microsoft:%'
             OR external_ref LIKE 'google:%'
           )
        """
    )


def downgrade() -> None:
    # The migration cannot distinguish these rows from records a user deleted
    # after upgrade, so downgrade must not revive either class of record.
    pass
