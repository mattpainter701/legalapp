"""Firm-wide email intake, preserving the tenant-scoped review queue."""
from alembic import op
import sqlalchemy as sa

revision = "184_firm_email_intake"
# Re-sequence against the merged hotfix before promotion.
down_revision = "183_drawn_signature"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_inbound_alias_kind", "inbound_email_aliases", type_="check")
    op.alter_column("inbound_email_aliases", "matter_id", nullable=True)
    op.alter_column("inbound_emails", "matter_id", nullable=True)
    op.create_check_constraint("ck_inbound_alias_kind", "inbound_email_aliases",
        "(kind = 'matter' AND matter_id IS NOT NULL) OR (kind = 'firm' AND matter_id IS NULL)")
    op.create_index("uq_inbound_alias_active_firm", "inbound_email_aliases", ["tenant_id"],
        unique=True, postgresql_where=sa.text("status = 'active' AND kind = 'firm'"))


def downgrade():
    # Do not silently destroy unfiled correspondence or retained address history.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM inbound_email_aliases WHERE kind='firm') THEN RAISE EXCEPTION 'Retain firm intake evidence; use forward repair'; END IF; END $$")
    op.drop_index("uq_inbound_alias_active_firm", table_name="inbound_email_aliases")
    op.drop_constraint("ck_inbound_alias_kind", "inbound_email_aliases", type_="check")
    op.alter_column("inbound_emails", "matter_id", nullable=False)
    op.alter_column("inbound_email_aliases", "matter_id", nullable=False)
    op.create_check_constraint("ck_inbound_alias_kind", "inbound_email_aliases", "kind = 'matter'")
