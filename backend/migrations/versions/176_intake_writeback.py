"""Structured intake write-back proposals on the intake packet.

A submitted client questionnaire already stored its answers as plain text and
JSON, but nothing mapped those answers back onto the Contact and Matter
records.  This column holds the derived proposal set: each entry names the
record field, the value the client supplied, the value on file, and whether
the proposal fills an empty field or conflicts with a curated one.  Staff
accept or reject every entry before any write is applied; the conflict-check
record id is referenced here so reviewers see potential conflicts alongside
the proposals.

Revision ID: 176_intake_writeback
Revises: 175_document_template_sets
"""

from alembic import op


revision = "176_intake_writeback"
down_revision = "175_document_template_sets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE matter_intakes ADD COLUMN proposed_changes jsonb "
        "NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade():
    op.execute("ALTER TABLE matter_intakes DROP COLUMN proposed_changes")
