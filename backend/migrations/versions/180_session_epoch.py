"""Per-user session epoch.

Ending a session used to be impossible to express. A password reset changed the
hash and nothing else, so a rotating refresh chain an attacker already held kept
renewing itself for its full idle window — the one action a user takes when they
believe they are compromised did not log the attacker out.

``sessions_valid_after`` is the instant before which every credential that user
holds is void: access tokens are compared by their ``iat``, refresh chains by
the origin timestamp each rotation now carries. It is set on password reset and
on signing out everywhere.

Nullable and additive. NULL means the user has never ended a session, which is
the correct reading for every existing row.

Revision ID: 180_session_epoch
Revises: 179_native_signing
"""

import sqlalchemy as sa
from alembic import op

revision = "180_session_epoch"
down_revision = "179_native_signing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("sessions_valid_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("users", "sessions_valid_after")
