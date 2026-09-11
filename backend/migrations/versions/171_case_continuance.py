"""Carry a case past intake: firm portal replies, dated signatures, shared files.

Intake was the only structured conversation a matter had. These three columns
open the rest of the case:

* ``matters.portal_messages_seen_at`` -- when the firm last read this matter's
  portal thread.  The marker is per matter rather than per user: a shared
  inbox is how a firm reads client mail, and a message one clerk has handled
  should not keep nagging the rest of the team.
* ``signature_requests.due_at`` -- the date a signature is wanted by, which
  raises the same kind of assigned follow-up task an intake requirement does.
  Distinct from ``expires_at``, which voids the request; a passed due date
  only means somebody should chase it.
* ``matter_documents.portal_shared_at`` -- when a document was first made
  visible to the client, so sharing can tell the client once and never twice.

Revision ID: 171_case_continuance
Revises: 170_matter_number
"""

from alembic import op


revision = "171_case_continuance"
down_revision = "170_matter_number"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE matters ADD COLUMN portal_messages_seen_at timestamptz NULL"
    )
    op.execute("ALTER TABLE signature_requests ADD COLUMN due_at timestamptz NULL")
    op.execute(
        "ALTER TABLE matter_documents ADD COLUMN portal_shared_at timestamptz NULL"
    )
    # A document already visible to the client was shared before this column
    # existed. Stamping it keeps the first-share notification from firing for
    # every historical document the moment this deploys.
    op.execute(
        "UPDATE matter_documents SET portal_shared_at = COALESCE(updated_at, created_at) "
        "WHERE portal_visible IS TRUE"
    )


def downgrade():
    op.execute("ALTER TABLE matter_documents DROP COLUMN portal_shared_at")
    op.execute("ALTER TABLE signature_requests DROP COLUMN due_at")
    op.execute("ALTER TABLE matters DROP COLUMN portal_messages_seen_at")
