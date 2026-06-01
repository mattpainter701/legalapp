"""Add tenant_credentials and user_oauth_tokens tables for OAuth token persistence.

Revision ID: 009
Revises: 008
Create Date: 2026-06-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_credentials",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("service_account_email", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
    )
    op.create_index(
        "ix_tenant_credentials_tenant_provider",
        "tenant_credentials",
        ["tenant_id", "provider"],
        unique=True,
    )

    op.create_table(
        "user_oauth_tokens",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
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
    )
    op.create_index(
        "ix_user_oauth_tokens_user_provider",
        "user_oauth_tokens",
        ["user_id", "provider"],
        unique=True,
    )
    op.create_index(
        "ix_user_oauth_tokens_tenant_id",
        "user_oauth_tokens",
        ["tenant_id"],
    )

    # Row-Level Security
    op.execute("ALTER TABLE tenant_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_credentials FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_tenant_credentials ON tenant_credentials
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    op.execute("ALTER TABLE user_oauth_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_oauth_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_user_oauth_tokens ON user_oauth_tokens
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    for table in ("user_oauth_tokens", "tenant_credentials"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("user_oauth_tokens")
    op.drop_table("tenant_credentials")
