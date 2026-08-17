"""add operator API keys minted by the platform console

Additive only: creates one new operator-owned table. No tenant table is
touched, so this is safe to apply ahead of the application deploy.

Revision ID: 108_platform_operator_api_keys
Revises: 107_access_log_request_id
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "108_platform_operator_api_keys"
down_revision = "107_access_log_request_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("key_prefix", sa.String(length=24), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=120), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_platform_api_keys_key_hash",
        "platform_api_keys",
        ["key_hash"],
        unique=True,
    )
    op.create_index(
        "idx_platform_api_keys_created_at",
        "platform_api_keys",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_platform_api_keys_created_at", table_name="platform_api_keys")
    op.drop_index("idx_platform_api_keys_key_hash", table_name="platform_api_keys")
    op.drop_table("platform_api_keys")
