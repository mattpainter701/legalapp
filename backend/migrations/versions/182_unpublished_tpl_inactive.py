"""Deactivate templates that were never published.

Generating a document reads the template's published version, and refuses a
template that has none. Rows created after the publication lifecycle landed
still took the column default ``is_active = true`` — the intake starter pack
did this for every firm — so pickers listed them as generatable while every
render answered 409 "Publish a tested version". Such a row is a draft: it is
inactive until an attorney tests and publishes it, exactly as the migration
that introduced the lifecycle left every pre-existing draft.

Data only; no schema change. The downgrade cannot tell these rows from any
other inactive draft and leaves them as they are.

Revision ID: 182_unpublished_tpl_inactive
Revises: 181_session_epoch
"""

from alembic import op

revision = "182_unpublished_tpl_inactive"
down_revision = "181_session_epoch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE document_templates
        SET is_active = false,
            status = CASE WHEN status = 'published' THEN 'draft' ELSE status END
        WHERE is_active AND published_version_no IS NULL
        """
    )


def downgrade() -> None:
    pass
