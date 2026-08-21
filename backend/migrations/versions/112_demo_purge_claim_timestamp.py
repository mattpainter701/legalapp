"""record when a demo purge worker claimed a session

Additive and nullable: existing rows keep a NULL purge_started_at.  The purge
claim guard needs to know how long a session has been claimed, not how long
the tenant has been expired — a tenant that expired well before its first
purge attempt would otherwise look reclaimable the instant a live worker
claimed it, defeating the serialization guard.

Rows already stranded in "purging" are backfilled to now() so the reclaim
window starts at the deploy rather than firing immediately.

Revision ID: 112_demo_purge_claim
Revises: 111_client_crm_management

Revision ids are capped at 32 characters by alembic_version.version_num.
"""

from alembic import op
import sqlalchemy as sa

revision = "112_demo_purge_claim"
down_revision = "111_client_crm_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "demo_sessions",
        sa.Column("purge_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE demo_sessions SET purge_started_at = now() "
        "WHERE status = 'purging' AND purge_started_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("demo_sessions", "purge_started_at")
