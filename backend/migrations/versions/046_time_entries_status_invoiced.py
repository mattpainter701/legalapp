"""Migrate time_entries.status from 'billed' to 'invoiced'."""

from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE time_entries SET status = 'invoiced' WHERE status = 'billed'")


def downgrade():
    op.execute("UPDATE time_entries SET status = 'billed' WHERE status = 'invoiced'")
