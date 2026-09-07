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
    # Retain the deployed TEXT type for rolling-release compatibility. The cap
    # is an additive database constraint, after a scoped preview-only backfill.
    op.execute("UPDATE cloud_metadata_index SET snippet = left(snippet, 500) WHERE char_length(snippet) > 500")
    op.create_check_constraint("ck_cloud_metadata_snippet_length", "cloud_metadata_index", "snippet IS NULL OR char_length(snippet) <= 500")


def downgrade():
    op.drop_constraint("ck_cloud_metadata_snippet_length", "cloud_metadata_index", type_="check")
    op.drop_column("tenant_credentials", "account_detected_at")
    op.drop_column("tenant_credentials", "account_domain")
    op.drop_column("tenant_credentials", "account_type")
