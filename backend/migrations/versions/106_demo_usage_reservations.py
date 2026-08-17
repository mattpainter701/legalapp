"""add atomic demo usage reservation ledger

Revision ID: 106_demo_usage_reservations
Revises: 105_live_demo_foundation
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "106_demo_usage_reservations"
down_revision = "105_live_demo_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_usage_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("surface", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="reserved", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'released')",
            name="ck_demo_usage_reservations_status",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["demo_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "idempotency_key", name="uq_demo_usage_session_key"),
    )
    op.create_index(
        "idx_demo_usage_tenant_status",
        "demo_usage_reservations",
        ["tenant_id", "status"],
    )
    op.execute("ALTER TABLE demo_usage_reservations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE demo_usage_reservations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_demo_usage_reservations
        ON demo_usage_reservations
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.drop_index("idx_demo_usage_tenant_status", table_name="demo_usage_reservations")
    op.drop_table("demo_usage_reservations")

