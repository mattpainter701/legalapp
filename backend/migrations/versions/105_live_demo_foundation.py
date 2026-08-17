"""Add tenant expiry and normalized demo-session lifecycle state.

Revision ID: 105_live_demo_foundation
Revises: 104_user_professional_context
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "105_live_demo_foundation"
down_revision = "104_user_professional_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tenants_expires_at", "tenants", ["expires_at"])

    op.create_table(
        "demo_sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fixture_tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("fixture_version", sa.String(80), nullable=False),
        sa.Column("prospect_name", sa.String(255), nullable=False),
        sa.Column("prospect_email", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.String(24),
            nullable=False,
            server_default="provisioning",
        ),
        sa.Column("quota", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('provisioning', 'active', 'expired', 'purging', 'purged', 'failed')",
            name="ck_demo_sessions_status",
        ),
        sa.CheckConstraint("quota > 0", name="ck_demo_sessions_quota_positive"),
        sa.CheckConstraint(
            "reserved >= 0 AND used >= 0 AND reserved + used <= quota",
            name="ck_demo_sessions_quota_counters",
        ),
        sa.UniqueConstraint("tenant_id", name="uq_demo_sessions_tenant_id"),
    )
    op.create_index("ix_demo_sessions_tenant_id", "demo_sessions", ["tenant_id"])
    op.create_index(
        "ix_demo_sessions_fixture_tenant_id",
        "demo_sessions",
        ["fixture_tenant_id"],
    )
    op.create_index("ix_demo_sessions_expires_at", "demo_sessions", ["expires_at"])
    op.create_index(
        "idx_demo_sessions_status_expires",
        "demo_sessions",
        ["status", "expires_at"],
    )
    op.execute("ALTER TABLE demo_sessions ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY demo_sessions_tenant_isolation ON demo_sessions
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        """
    )
    op.execute("ALTER TABLE demo_sessions FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("demo_sessions")
    op.drop_index("ix_tenants_expires_at", table_name="tenants")
    op.drop_column("tenants", "expires_at")
