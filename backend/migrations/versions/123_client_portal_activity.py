"""Client portal activity tracking: last seen + messages seen timestamps.

The portal had no notion of what the client had already looked at, so neither
side could tell a fresh message from an old one. Two nullable timestamps on the
invite row are enough:

  - ``last_seen_at``    — most recent portal request, surfaced to the firm so
                          staff can see whether an invite is actually being used.
  - ``messages_seen_at`` — high-water mark the client has read up to, which is
                          what the portal's unread badge counts against.

Both are additive and nullable; existing invites simply read as "never seen",
which is the correct answer for a portal that never tracked this before.

Revision ID: 123_client_portal_activity
Revises: 122_teams_voice_capture
"""

from alembic import op
import sqlalchemy as sa


revision = "123_client_portal_activity"
down_revision = "122_teams_voice_capture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "client_portal_invites",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "client_portal_invites",
        sa.Column("messages_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("client_portal_invites", "messages_seen_at")
    op.drop_column("client_portal_invites", "last_seen_at")
