"""036 — Matter management rebuild: partner attorney, retention, active-working, matter-scoped conversations.

Revision ID: 036
Revises: 035
Create Date: 2026-06-05

Changes:
  matters:
    + partner_attorney_id   UUID nullable FK → users.id ON DELETE SET NULL
    + retention_until       DATE nullable (default 7yr from created_at)
    + archived_at           TIMESTAMPTZ nullable

  matter_assignments:
    + is_active_working     BOOLEAN default false (paralegal "I'm on this now" flag)

  conversations:
    + matter_id             UUID nullable FK → matters.id ON DELETE SET NULL
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── matters: partner attorney ───────────────────────────────────────────────
    op.add_column(
        "matters",
        sa.Column(
            "partner_attorney_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_matters_partner_attorney", "matters", ["partner_attorney_id"]
    )

    # ── matters: retention / archival ───────────────────────────────────────────
    op.add_column(
        "matters",
        sa.Column("retention_until", sa.Date, nullable=True),
    )
    op.add_column(
        "matters",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Back-fill retention_until = created_at + 7 years for existing matters
    op.execute(
        "UPDATE matters SET retention_until = (created_at + INTERVAL '7 years')::date "
        "WHERE retention_until IS NULL"
    )

    # ── matter_assignments: active-working flag ─────────────────────────────────
    op.add_column(
        "matter_assignments",
        sa.Column(
            "is_active_working",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )

    # ── conversations: matter scope ─────────────────────────────────────────────
    op.add_column(
        "conversations",
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_conversations_matter_id", "conversations", ["matter_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_conversations_matter_id", table_name="conversations")
    op.drop_column("conversations", "matter_id")

    op.drop_column("matter_assignments", "is_active_working")

    op.drop_column("matters", "archived_at")
    op.drop_column("matters", "retention_until")
    op.drop_index("idx_matters_partner_attorney", table_name="matters")
    op.drop_column("matters", "partner_attorney_id")
