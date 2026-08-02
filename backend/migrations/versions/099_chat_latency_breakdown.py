"""Add structured chat latency telemetry.

Revision ID: 099_chat_latency_breakdown
Revises: 098_plugin_skill_runs
"""

from alembic import op
import sqlalchemy as sa


revision = "099_chat_latency_breakdown"
down_revision = "098_plugin_skill_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_records",
        sa.Column("latency_breakdown", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("usage_records", "latency_breakdown")
