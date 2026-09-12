"""Add a free-text venue to matters.

Fee agreements name the forum where a fee action may be brought, and Smart
Fill can only fill that placeholder from a matter record if the matter carries
it. Free text, like jurisdiction: a venue is often a county plus a state, not
a court name the way ``court`` holds.

Revision ID: 177_matter_venue
Revises: 175_document_template_sets
"""

from alembic import op


revision = "177_matter_venue"
down_revision = "176_intake_writeback"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE matters ADD COLUMN venue varchar(300) NULL")


def downgrade():
    op.execute("ALTER TABLE matters DROP COLUMN venue")
