"""hide historical mailbox-sync mail that was never linked to a matter

The mailbox syncer used to archive every inbound message it could see, not
just mail from a known sender.  Those rows carry no matter and no contact, so
they are noise in the Communications workspace and — worse — they expose mail
the firm never chose to file.  ``/api/communications`` hides rows whose status
is ``deleted``, so flipping the status is enough to retire them.

The flip is recorded first, in ``communication_log_sync_hides``, so the
downgrade restores exactly the rows this migration touched and nothing else.
Without that record a downgrade could not tell a row we hid from one a user
deleted by hand, and "reversible" would be a claim rather than a fact.

``communication_logs`` carries FORCE row level security and a policy keyed on
``app.tenant_id``, which migrations run with set to a non-customer sentinel.
The backfill therefore drops FORCE for the duration and restores it, exactly
as 089/090/092 do; without that window the statement matches no rows at all
and the cleanup silently does nothing.

Revision ID: 109_hide_unlinked_synced_emails
Revises: 108_platform_operator_api_keys
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "109_hide_unlinked_synced_emails"
down_revision = "108_platform_operator_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "communication_log_sync_hides",
        sa.Column(
            "communication_log_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_status", sa.String(length=30), nullable=False),
        sa.Column(
            "hidden_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["communication_log_id"], ["communication_logs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("communication_log_id"),
    )
    op.create_index(
        "idx_commlog_sync_hides_tenant",
        "communication_log_sync_hides",
        ["tenant_id"],
    )

    # The ledger is written below under the owner role, before its own policy
    # exists; enabling RLS first would filter the INSERT to the sentinel tenant.
    op.execute("ALTER TABLE communication_logs NO FORCE ROW LEVEL SECURITY")

    # Record before mutating, and drive the UPDATE off what was recorded, so a
    # row is only ever hidden once its restore path exists.  The bounds keep
    # this to syncer-created mail (provider-prefixed external_ref) that never
    # acquired a matter or a contact; anything filed by hand is untouched.
    op.execute(
        """
        WITH recorded AS (
            INSERT INTO communication_log_sync_hides
                (communication_log_id, tenant_id, previous_status)
            SELECT id, tenant_id, status
              FROM communication_logs
             WHERE channel = 'email'
               AND direction = 'inbound'
               AND status <> 'deleted'
               AND matter_id IS NULL
               AND contact_id IS NULL
               AND (
                 external_ref LIKE 'microsoft:%'
                 OR external_ref LIKE 'google:%'
               )
            RETURNING communication_log_id
        )
        UPDATE communication_logs AS c
           SET status = 'deleted', updated_at = now()
          FROM recorded AS r
         WHERE c.id = r.communication_log_id
        """
    )

    op.execute("ALTER TABLE communication_logs FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE communication_log_sync_hides ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE communication_log_sync_hides FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_communication_log_sync_hides
        ON communication_log_sync_hides
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    # Same sentinel-tenant problem as the upgrade: without dropping FORCE the
    # restore would match nothing and quietly leave the rows hidden.
    op.execute("ALTER TABLE communication_logs NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE communication_log_sync_hides NO FORCE ROW LEVEL SECURITY")

    # Restore only what this migration hid, and only if it is still hidden.
    # A row we hid was invisible in the workspace afterwards, so a user could
    # not have re-deleted it; anything no longer 'deleted' was revived by hand
    # and must keep the status it was given.
    op.execute(
        """
        UPDATE communication_logs AS c
           SET status = h.previous_status, updated_at = now()
          FROM communication_log_sync_hides AS h
         WHERE c.id = h.communication_log_id
           AND c.status = 'deleted'
        """
    )

    op.execute("ALTER TABLE communication_logs FORCE ROW LEVEL SECURITY")

    op.drop_index(
        "idx_commlog_sync_hides_tenant", table_name="communication_log_sync_hides"
    )
    op.drop_table("communication_log_sync_hides")
