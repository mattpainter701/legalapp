"""033 — Matter core revamp: description, attorney_of_record, memory, relax required fields.

Revision ID: 033
Revises: 032
Create Date: 2026-06-04

Changes:
  matters:
    + description            TEXT nullable
    + attorney_of_record_id  UUID nullable FK → users.id ON DELETE SET NULL
    + memory_content         TEXT nullable
    ~ counterparty           DROP NOT NULL (optional for non-litigation matters)
    ~ role                   DROP NOT NULL
    ~ jurisdiction           DROP NOT NULL
    ~ status default         changed from 'threatened' to 'open'
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── New columns ─────────────────────────────────────────────────────────────
    op.add_column("matters", sa.Column("description", sa.Text, nullable=True))
    op.add_column(
        "matters",
        sa.Column(
            "attorney_of_record_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("matters", sa.Column("memory_content", sa.Text, nullable=True))

    # ── Relax formerly-required fields ─────────────────────────────────────────
    op.alter_column("matters", "counterparty", nullable=True)
    op.alter_column("matters", "role", nullable=True)
    op.alter_column("matters", "jurisdiction", nullable=True)

    # ── Change status default ───────────────────────────────────────────────────
    op.alter_column(
        "matters",
        "status",
        server_default=sa.text("'open'"),
        existing_type=sa.String(100),
        existing_nullable=False,
    )

    # ── Index on attorney_of_record_id ─────────────────────────────────────────
    op.create_index(
        "idx_matters_attorney_of_record", "matters", ["attorney_of_record_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_matters_attorney_of_record", table_name="matters")

    op.alter_column(
        "matters",
        "status",
        server_default=sa.text("'threatened'"),
        existing_type=sa.String(100),
        existing_nullable=False,
    )

    op.alter_column("matters", "jurisdiction", nullable=False)
    op.alter_column("matters", "role", nullable=False)
    op.alter_column("matters", "counterparty", nullable=False)

    op.drop_column("matters", "memory_content")
    op.drop_column("matters", "attorney_of_record_id")
    op.drop_column("matters", "description")
