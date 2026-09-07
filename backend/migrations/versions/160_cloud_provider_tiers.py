"""Persist account tiers and enforce the existing 500-character preview limit."""
from alembic import op
import sqlalchemy as sa

revision = "160_cloud_provider_tiers"
down_revision = "159_navigation_profiles"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tenant_credentials", sa.Column("account_type", sa.String(20)))
    op.add_column("tenant_credentials", sa.Column("account_domain", sa.String(255)))
    op.add_column("tenant_credentials", sa.Column("account_detected_at", sa.DateTime(timezone=True)))
    # Explicitly trim legacy oversized previews before narrowing the column.
    op.alter_column("cloud_metadata_index", "snippet", existing_type=sa.Text(), type_=sa.String(500), postgresql_using="left(snippet, 500)")


def downgrade():
    op.alter_column("cloud_metadata_index", "snippet", existing_type=sa.String(500), type_=sa.Text())
    op.drop_column("tenant_credentials", "account_detected_at")
    op.drop_column("tenant_credentials", "account_domain")
    op.drop_column("tenant_credentials", "account_type")
