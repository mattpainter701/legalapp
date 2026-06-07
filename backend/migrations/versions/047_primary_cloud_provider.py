"""Add primary_cloud_provider to tenant_settings."""

import sqlalchemy as sa
from alembic import op

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tenant_settings",
        sa.Column("primary_cloud_provider", sa.String(50), nullable=True),
    )


def downgrade():
    op.drop_column("tenant_settings", "primary_cloud_provider")
