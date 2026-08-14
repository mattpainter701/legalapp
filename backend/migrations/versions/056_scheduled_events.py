"""Create scheduled_events for meeting provider calendar events.

Revision ID: 056
Revises: 055
Create Date: 2026-06-15 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "scheduled_events",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"),
        sa.Column("attendees", postgresql.JSONB(), nullable=True),
        sa.Column("calendar_provider", sa.String(50), nullable=True),
        sa.Column(
            "meeting_provider", sa.String(50), nullable=False, server_default="none"
        ),
        sa.Column("external_calendar_event_id", sa.String(500), nullable=True),
        sa.Column("external_calendar_url", sa.Text(), nullable=True),
        sa.Column("meeting_id", sa.String(500), nullable=True),
        sa.Column("join_url", sa.Text(), nullable=True),
        sa.Column(
            "sync_status", sa.String(50), nullable=False, server_default="pending"
        ),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "idx_scheduled_events_tenant_start",
        "scheduled_events",
        ["tenant_id", "start_at"],
    )
    op.create_index(
        "idx_scheduled_events_matter_id",
        "scheduled_events",
        ["tenant_id", "matter_id"],
    )
    op.create_index(
        "idx_scheduled_events_created_by",
        "scheduled_events",
        ["tenant_id", "created_by_user_id"],
    )
    _enable_rls("scheduled_events")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_scheduled_events ON scheduled_events"
    )
    op.execute("ALTER TABLE scheduled_events DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_scheduled_events_created_by", table_name="scheduled_events")
    op.drop_index("idx_scheduled_events_matter_id", table_name="scheduled_events")
    op.drop_index("idx_scheduled_events_tenant_start", table_name="scheduled_events")
    op.drop_table("scheduled_events")
