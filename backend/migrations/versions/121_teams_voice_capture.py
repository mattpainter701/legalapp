"""Teams Phone (voice) capture: per-tenant settings + call-log idempotency.

Teams call records arrive on an application-permission Graph surface, so voice
capture needs its own per-tenant configuration row (Entra tenant GUID, change
notification subscription state, clientState secret) rather than riding the
delegated Teams credential.

The partial unique index mirrors the Zoom Phone one: it makes
``teams_voice:call:<id>`` the idempotency key for captured calls so the webhook
path and the reconciliation sweep converge on a single communication log.

Revision ID: 121_teams_voice_capture
Revises: 120_marketing_demo_funnel
"""

from alembic import op
import sqlalchemy as sa


revision = "121_teams_voice_capture"
down_revision = "120_marketing_demo_funnel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams_voice_settings",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("entra_tenant_id", sa.String(64), nullable=True),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("encrypted_client_state", sa.Text(), nullable=True),
        sa.Column("subscription_id", sa.String(255), nullable=True),
        sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_url", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(30), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("last_call_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("configured_by_user_id", sa.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["configured_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("tenant_id", name="uq_teams_voice_settings_tenant"),
    )
    # Every notification arrives with only a Graph subscription id; resolving it
    # back to a tenant is on the hot path of the webhook.
    op.create_index(
        "idx_teams_voice_settings_subscription",
        "teams_voice_settings",
        ["subscription_id"],
    )
    op.execute("ALTER TABLE teams_voice_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE teams_voice_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_teams_voice_settings ON teams_voice_settings
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_commlogs_teams_voice_external_ref
        ON communication_logs (tenant_id, external_ref)
        WHERE external_ref LIKE 'teams_voice:call:%'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_commlogs_teams_voice_external_ref")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_teams_voice_settings "
        "ON teams_voice_settings"
    )
    op.execute("ALTER TABLE teams_voice_settings DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "idx_teams_voice_settings_subscription", table_name="teams_voice_settings"
    )
    op.drop_table("teams_voice_settings")
