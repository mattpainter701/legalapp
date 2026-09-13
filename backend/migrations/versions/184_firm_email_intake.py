"""Add firm addresses without replacing any deployed matter-alias constraint."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "184_firm_email_intake"
down_revision = "183_drawn_signature"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("firm_inbound_email_aliases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("encrypted_local_part", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("last_received_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_firm_inbound_alias_status"))
    op.create_index("uq_firm_inbound_alias_active", "firm_inbound_email_aliases", ["tenant_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"))
    op.execute("ALTER TABLE firm_inbound_email_aliases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE firm_inbound_email_aliases FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY firm_inbound_email_aliases_tenant_isolation ON firm_inbound_email_aliases
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)""")
    op.execute("""CREATE POLICY firm_inbound_email_aliases_route_lookup ON firm_inbound_email_aliases
        FOR SELECT USING (current_setting('app.inbound_email_route_lookup', true) = 'on')""")
    op.add_column("inbound_emails", sa.Column("firm_alias_id", UUID(as_uuid=True),
        sa.ForeignKey("firm_inbound_email_aliases.id", ondelete="RESTRICT"), nullable=True))
    op.alter_column("inbound_emails", "alias_id", nullable=True)
    op.alter_column("inbound_emails", "matter_id", nullable=True)
    op.create_unique_constraint("uq_inbound_email_firm_sha256", "inbound_emails", ["firm_alias_id", "message_sha256"])
    op.create_check_constraint("ck_inbound_alias_source", "inbound_emails",
        "(alias_id IS NOT NULL AND firm_alias_id IS NULL AND matter_id IS NOT NULL) OR (alias_id IS NULL AND firm_alias_id IS NOT NULL)")
    op.create_check_constraint("ck_inbound_accepted_matter", "inbound_emails",
        "status != 'accepted' OR matter_id IS NOT NULL")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM firm_inbound_email_aliases) THEN RAISE EXCEPTION 'Retain firm intake evidence; use forward repair'; END IF; END $$")
    op.drop_constraint("ck_inbound_accepted_matter", "inbound_emails", type_="check")
    op.drop_constraint("ck_inbound_alias_source", "inbound_emails", type_="check")
    op.drop_constraint("uq_inbound_email_firm_sha256", "inbound_emails", type_="unique")
    op.alter_column("inbound_emails", "alias_id", nullable=False)
    op.alter_column("inbound_emails", "matter_id", nullable=False)
    op.drop_column("inbound_emails", "firm_alias_id")
    op.drop_table("firm_inbound_email_aliases")
