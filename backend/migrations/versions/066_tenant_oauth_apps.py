"""Add tenant-owned OAuth app credentials.

Revision ID: 066
Revises: 065
Create Date: 2026-06-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "tenant_oauth_apps",
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
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("encrypted_client_id", sa.Text(), nullable=False),
        sa.Column("encrypted_client_secret", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column(
            "configured_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.UniqueConstraint(
            "tenant_id", "provider", name="uq_tenant_oauth_apps_provider"
        ),
    )
    op.create_index("idx_tenant_oauth_apps_tenant", "tenant_oauth_apps", ["tenant_id"])
    _enable_rls("tenant_oauth_apps")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_oauth_apps_tenant_isolation ON tenant_oauth_apps"
    )
    op.drop_index("idx_tenant_oauth_apps_tenant", table_name="tenant_oauth_apps")
    op.drop_table("tenant_oauth_apps")
