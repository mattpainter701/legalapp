"""056 — Matter email correspondence capture.

Adds per-matter email correspondence capture support:
- ``matters.correspondence_rules`` (JSONB): per-matter capture config controlling
  which emails are archived (party-address match, case-number match, plus
  reserved keys for future keyword / direction filtering).
- ``communication_logs.document_id``: FK to the stored ``.eml`` MatterDocument.
- ``communication_logs.thread_ref``: provider conversation/thread id used to
  group captured emails into conversation chains.
- ``communication_logs.participants`` (JSONB): {from, to, cc} so the UI can show
  who said what without a contacts join.

All columns are nullable — existing rows and matters are unaffected. No new
tables, so no RLS policy changes are required (both tables already have RLS).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matters",
        sa.Column("correspondence_rules", JSONB(), nullable=True),
    )
    op.add_column(
        "communication_logs",
        sa.Column("document_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_commlogs_document_id",
        "communication_logs",
        "matter_documents",
        ["document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "communication_logs",
        sa.Column("thread_ref", sa.String(500), nullable=True),
    )
    op.add_column(
        "communication_logs",
        sa.Column("participants", JSONB(), nullable=True),
    )
    op.create_index(
        "idx_commlogs_thread_ref",
        "communication_logs",
        ["tenant_id", "thread_ref"],
    )


def downgrade() -> None:
    op.drop_index("idx_commlogs_thread_ref", table_name="communication_logs")
    op.drop_column("communication_logs", "participants")
    op.drop_column("communication_logs", "thread_ref")
    op.drop_constraint(
        "fk_commlogs_document_id", "communication_logs", type_="foreignkey"
    )
    op.drop_column("communication_logs", "document_id")
    op.drop_column("matters", "correspondence_rules")
