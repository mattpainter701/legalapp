"""Public intake, consent, booking and funnel evidence for COMP-03."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "142_conversion_loop"
down_revision = "141_esign_webhook_events"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("intake_forms", sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("slug", sa.String(120), nullable=False), sa.Column("name", sa.String(200), nullable=False), sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("schema_json", JSONB(), nullable=False, server_default="{}"), sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"), sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.UniqueConstraint("slug", name="uq_intake_forms_slug"))
    op.create_table("intake_submissions", sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("form_id", UUID(as_uuid=True), sa.ForeignKey("intake_forms.id", ondelete="CASCADE"), nullable=False), sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="SET NULL")), sa.Column("idempotency_key", sa.String(200), nullable=False), sa.Column("answers", JSONB(), nullable=False, server_default="{}"), sa.Column("attribution", JSONB(), nullable=False, server_default="{}"), sa.Column("source_ip_hash", sa.String(128)), sa.Column("status", sa.String(30), nullable=False, server_default="accepted"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.UniqueConstraint("tenant_id", "idempotency_key"))
    op.create_table("lead_channel_consents", sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False), sa.Column("email_allowed", sa.Boolean(), nullable=False, server_default="false"), sa.Column("sms_allowed", sa.Boolean(), nullable=False, server_default="false"), sa.Column("phone_verified", sa.Boolean(), nullable=False, server_default="false"), sa.Column("disclosure_version", sa.String(80)), sa.Column("source", sa.String(40), nullable=False, server_default="public_intake"), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.UniqueConstraint("tenant_id", "lead_id"))
    op.create_table("lead_appointments", sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False), sa.Column("scheduled_event_id", UUID(as_uuid=True), sa.ForeignKey("scheduled_events.id", ondelete="SET NULL")), sa.Column("idempotency_key", sa.String(200), nullable=False), sa.Column("start_at", sa.DateTime(timezone=True), nullable=False), sa.Column("end_at", sa.DateTime(timezone=True), nullable=False), sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"), sa.Column("status", sa.String(30), nullable=False, server_default="booked"), sa.Column("reminder_status", sa.String(30), nullable=False, server_default="pending"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.UniqueConstraint("tenant_id", "idempotency_key"))
    op.create_table("lead_funnel_events", sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")), sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="SET NULL")), sa.Column("event_type", sa.String(50), nullable=False), sa.Column("source", sa.String(120)), sa.Column("metadata_json", JSONB(), nullable=False, server_default="{}"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    for table in ("intake_forms", "intake_submissions", "lead_channel_consents", "lead_appointments", "lead_funnel_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)")
    # The public slug is the only lookup key available before a tenant is
    # known. Only active form definitions (never submissions or answers) are
    # readable without a tenant context; writes still require the resolved
    # tenant policy above.
    op.execute("CREATE POLICY intake_forms_public_read ON intake_forms FOR SELECT USING (is_active IS TRUE)")
    op.create_index("idx_intake_forms_public", "intake_forms", ["slug", "is_active"])
    op.create_index("idx_lead_funnel_events_tenant_created", "lead_funnel_events", ["tenant_id", "created_at"])

def downgrade():
    for table in ("lead_funnel_events", "lead_appointments", "lead_channel_consents", "intake_submissions", "intake_forms"):
        op.drop_table(table)
