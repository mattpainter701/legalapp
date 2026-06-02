"""Create qbo_integrations table for QuickBooks Online OAuth2 tokens.

Revision ID: 016
Revises: 015
Create Date: 2026-06-02 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qbo_integrations",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "qbo_realm_id",
            sa.String(100),
            nullable=True,
            comment="QBO Company ID returned from OAuth",
        ),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "sync_frequency_minutes", sa.Integer(), nullable=False, server_default="15"
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_sync_status",
            sa.String(50),
            nullable=True,
            comment="success, partial, failed",
        ),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column(
            "sandbox_mode",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="True when connected to QBO sandbox, false for production",
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
    )
    op.create_index(
        "uq_qbo_integrations_tenant_id",
        "qbo_integrations",
        ["tenant_id"],
        unique=True,
    )

    op.execute("ALTER TABLE qbo_integrations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE qbo_integrations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_qbo_integrations ON qbo_integrations
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_qbo_integrations ON qbo_integrations"
    )
    op.execute("ALTER TABLE qbo_integrations DISABLE ROW LEVEL SECURITY")
    op.drop_index("uq_qbo_integrations_tenant_id", table_name="qbo_integrations")
    op.drop_table("qbo_integrations")
