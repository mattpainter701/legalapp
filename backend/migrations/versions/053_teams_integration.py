"""Create Teams integration tables: channel links + notification settings.

Revision ID: 053
Revises: 052
Create Date: 2026-06-13 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "053"
down_revision: Union[str, None] = "052"
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
    # ── teams_channel_links ──────────────────────────────────────────────
    op.create_table(
        "teams_channel_links",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", sa.String(100), nullable=False),
        sa.Column("channel_id", sa.String(100), nullable=False),
        sa.Column("team_display_name", sa.String(255), nullable=True),
        sa.Column("channel_display_name", sa.String(255), nullable=True),
        sa.Column("tab_deep_link", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "tenant_id",
            "matter_id",
            "channel_id",
            name="uq_teams_channel_links_matter_channel",
        ),
    )
    op.create_index(
        "idx_teams_channel_links_tenant_id", "teams_channel_links", ["tenant_id"]
    )
    op.create_index(
        "idx_teams_channel_links_matter_id", "teams_channel_links", ["matter_id"]
    )
    _enable_rls("teams_channel_links")

    # ── teams_notification_settings ──────────────────────────────────────
    op.create_table(
        "teams_notification_settings",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("team_id", sa.String(100), nullable=False),
        sa.Column("channel_id", sa.String(100), nullable=False),
        sa.Column("team_display_name", sa.String(255), nullable=True),
        sa.Column("channel_display_name", sa.String(255), nullable=True),
        sa.Column("matter_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "tenant_id",
            "event_type",
            "channel_id",
            "matter_id",
            name="uq_teams_notif_event_channel",
        ),
    )
    op.create_index(
        "idx_teams_notif_settings_tenant_id",
        "teams_notification_settings",
        ["tenant_id"],
    )
    _enable_rls("teams_notification_settings")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_teams_notification_settings "
        "ON teams_notification_settings"
    )
    op.execute("ALTER TABLE teams_notification_settings DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "idx_teams_notif_settings_tenant_id",
        table_name="teams_notification_settings",
    )
    op.drop_table("teams_notification_settings")

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_teams_channel_links "
        "ON teams_channel_links"
    )
    op.execute("ALTER TABLE teams_channel_links DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_teams_channel_links_matter_id", table_name="teams_channel_links")
    op.drop_index("idx_teams_channel_links_tenant_id", table_name="teams_channel_links")
    op.drop_table("teams_channel_links")
