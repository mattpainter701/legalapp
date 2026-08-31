"""Tenant-scoped provider-backed SMS lifecycle and consent provenance."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "149_sms_lifecycle"
down_revision = "148_configurable_workflows"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"""
    )


def upgrade() -> None:
    # Parent composite keys make tenant ownership enforceable by PostgreSQL,
    # not merely by application predicates or RLS policies.
    # Migration 148 already owns contacts.uq_contacts_tenant_id. SMS adds only
    # the remaining composite target that did not exist at the prior head.
    op.create_unique_constraint(
        "uq_communication_logs_tenant_id",
        "communication_logs",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint("uq_leads_tenant_id", "leads", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_lead_channel_consents_tenant_id",
        "lead_channel_consents",
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        "fk_lead_channel_consents_tenant_lead",
        "lead_channel_consents",
        "leads",
        ["tenant_id", "lead_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "lead_channel_consents",
        sa.Column(
            "sms_status", sa.String(30), nullable=False, server_default="unknown"
        ),
    )
    op.add_column("lead_channel_consents", sa.Column("mobile_e164", sa.String(30)))
    op.add_column(
        "lead_channel_consents", sa.Column("consented_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "lead_channel_consents",
        sa.Column("consent_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "lead_channel_consents",
        sa.Column("sms_revoked_at", sa.DateTime(timezone=True)),
    )
    op.add_column("lead_channel_consents", sa.Column("consent_source", sa.String(80)))
    op.add_column("lead_channel_consents", sa.Column("consent_language", sa.String(20)))
    op.add_column(
        "lead_channel_consents", sa.Column("consent_timezone", sa.String(100))
    )
    op.add_column("lead_channel_consents", sa.Column("quiet_hours_start", sa.String(5)))
    op.add_column("lead_channel_consents", sa.Column("quiet_hours_end", sa.String(5)))
    op.add_column(
        "lead_channel_consents",
        sa.Column("allowed_categories", JSONB(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "sms_consent_events",
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
        sa.Column(
            "consent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("lead_channel_consents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("sms_status", sa.String(30), nullable=False),
        sa.Column("sms_allowed", sa.Boolean(), nullable=False),
        sa.Column("phone_verified", sa.Boolean(), nullable=False),
        sa.Column("mobile_e164", sa.String(30)),
        sa.Column("consented_at", sa.DateTime(timezone=True)),
        sa.Column("consent_expires_at", sa.DateTime(timezone=True)),
        sa.Column("sms_revoked_at", sa.DateTime(timezone=True)),
        sa.Column("consent_source", sa.String(80)),
        sa.Column("disclosure_version", sa.String(80)),
        sa.Column("consent_language", sa.String(20)),
        sa.Column("consent_timezone", sa.String(100)),
        sa.Column("quiet_hours_start", sa.String(5)),
        sa.Column("quiet_hours_end", sa.String(5)),
        sa.Column("allowed_categories", JSONB(), nullable=False, server_default="[]"),
        sa.Column("actor_type", sa.String(30), nullable=False),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sms_consent_events_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "consent_id"],
            ["lead_channel_consents.tenant_id", "lead_channel_consents.id"],
            name="fk_sms_consent_events_tenant_consent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "lead_id"],
            ["leads.tenant_id", "leads.id"],
            name="fk_sms_consent_events_tenant_lead",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            name="fk_sms_consent_events_tenant_contact",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_consent_events_tenant_user",
        ),
    )
    op.create_table(
        "sms_provider_configs",
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
        sa.Column("provider", sa.String(30), nullable=False, server_default="twilio"),
        sa.Column("account_sid", sa.String(100)),
        sa.Column("encrypted_auth_token", sa.Text()),
        sa.Column("messaging_service_sid", sa.String(100)),
        sa.Column("from_number", sa.String(30)),
        sa.Column("sender_ready", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("compliance_snapshot", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "updated_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
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
        sa.UniqueConstraint(
            "tenant_id", "provider", name="uq_sms_provider_configs_tenant_provider"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_provider_configs_tenant_user",
        ),
    )
    op.create_table(
        "sms_messages",
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
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "communication_log_id",
            UUID(as_uuid=True),
            sa.ForeignKey("communication_logs.id", ondelete="SET NULL"),
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("dispatch_attempt_id", UUID(as_uuid=True)),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_required_at", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_resolved_at", sa.DateTime(timezone=True)),
        sa.Column("reconciliation_resolution", sa.String(40)),
        sa.Column(
            "reconciliation_resolved_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="queued"),
        sa.Column("from_number", sa.String(30)),
        sa.Column("to_number", sa.String(30)),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "category", sa.String(50), nullable=False, server_default="staff_authored"
        ),
        sa.Column("provider_status", sa.String(40)),
        sa.Column("provider_error_code", sa.String(40)),
        sa.Column("segment_count", sa.Integer()),
        sa.Column("cost", sa.Numeric(12, 6)),
        sa.Column("raw_provider_event", JSONB(), nullable=False, server_default="{}"),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
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
        sa.UniqueConstraint("tenant_id", "id", name="uq_sms_messages_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_sms_messages_tenant_idempotency"
        ),
        sa.UniqueConstraint(
            "tenant_id", "provider_message_id", name="uq_sms_messages_provider_id"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            name="fk_sms_messages_tenant_contact",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            name="fk_sms_messages_tenant_matter",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "communication_log_id"],
            ["communication_logs.tenant_id", "communication_logs.id"],
            name="fk_sms_messages_tenant_communication",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_messages_tenant_user",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reconciliation_resolved_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_messages_tenant_reconciler",
        ),
    )
    op.create_table(
        "sms_review_items",
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
        sa.Column(
            "sms_message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sms_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column(
            "candidate_contact_ids", JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column("candidate_matter_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "reviewed_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "sms_message_id"],
            ["sms_messages.tenant_id", "sms_messages.id"],
            name="fk_sms_review_items_tenant_message",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reviewed_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_review_items_tenant_user",
        ),
    )
    op.add_column(
        "task_automation_runs", sa.Column("sms_message_id", UUID(as_uuid=True))
    )
    op.add_column(
        "task_automation_runs",
        sa.Column(
            "reconciliation_required",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.create_foreign_key(
        "fk_task_automation_runs_tenant_sms_message",
        "task_automation_runs",
        "sms_messages",
        ["tenant_id", "sms_message_id"],
        ["tenant_id", "id"],
    )
    op.create_index(
        "idx_sms_provider_configs_tenant",
        "sms_provider_configs",
        ["tenant_id", "is_active"],
    )
    op.create_index(
        "idx_sms_messages_tenant_contact",
        "sms_messages",
        ["tenant_id", "contact_id", "created_at"],
    )
    op.create_index(
        "idx_sms_messages_tenant_matter",
        "sms_messages",
        ["tenant_id", "matter_id", "created_at"],
    )
    op.create_index(
        "idx_sms_review_items_tenant_status",
        "sms_review_items",
        ["tenant_id", "status", "created_at"],
    )
    op.create_index(
        "idx_task_automation_runs_tenant_sms_message",
        "task_automation_runs",
        ["tenant_id", "sms_message_id"],
    )
    for table in (
        "sms_consent_events",
        "sms_provider_configs",
        "sms_messages",
        "sms_review_items",
    ):
        _rls(table)
    op.create_index(
        "idx_sms_consent_events_tenant_consent",
        "sms_consent_events",
        ["tenant_id", "consent_id", "occurred_at"],
    )
    op.create_index(
        "idx_sms_messages_reconciliation",
        "sms_messages",
        ["status", "dispatch_started_at"],
        postgresql_where=sa.text(
            "status IN ('dispatching', 'provider_unknown') AND direction = 'outbound'"
        ),
    )
    op.execute(
        """CREATE FUNCTION sms_demo_purge_authorized(row_tenant uuid) RETURNS boolean AS $$
        SELECT current_setting('app.sms_demo_purge_tenant_id', true) = row_tenant::text
          AND EXISTS (
            SELECT 1 FROM tenants tenant
            JOIN demo_sessions demo ON demo.tenant_id = tenant.id
            WHERE tenant.id = row_tenant
              AND tenant.billing_tier = 'demo'
              AND tenant.domain LIKE '%.demo.invalid'
              AND tenant.is_active = false
              AND tenant.expires_at <= now()
              AND demo.id::text = current_setting('app.sms_demo_purge_session_id', true)
              AND demo.status = 'purging'
              AND demo.fixture_tenant_id <> demo.tenant_id
              AND demo.purge_started_at IS NOT NULL
          );
        $$ LANGUAGE sql STABLE SET search_path = pg_catalog, public"""
    )
    op.execute(
        """CREATE FUNCTION prevent_sms_consent_event_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND public.sms_demo_purge_authorized(OLD.tenant_id)
          THEN RETURN OLD; END IF;
          RAISE EXCEPTION 'SMS consent evidence is immutable';
        END; $$ LANGUAGE plpgsql SET search_path = pg_catalog, public"""
    )
    op.execute(
        "CREATE TRIGGER sms_consent_events_immutable BEFORE UPDATE OR DELETE "
        "ON sms_consent_events FOR EACH ROW "
        "EXECUTE FUNCTION prevent_sms_consent_event_mutation()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS sms_consent_events_immutable ON sms_consent_events"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_sms_consent_event_mutation()")
    op.execute("DROP FUNCTION IF EXISTS sms_demo_purge_authorized(uuid)")
    op.drop_constraint(
        "fk_task_automation_runs_tenant_sms_message",
        "task_automation_runs",
        type_="foreignkey",
    )
    op.drop_column("task_automation_runs", "reconciliation_required")
    op.drop_column("task_automation_runs", "sms_message_id")
    for table in (
        "sms_review_items",
        "sms_messages",
        "sms_provider_configs",
        "sms_consent_events",
    ):
        op.drop_table(table)
    for name in (
        "allowed_categories",
        "quiet_hours_end",
        "quiet_hours_start",
        "consent_timezone",
        "consent_language",
        "consent_source",
        "consented_at",
        "sms_revoked_at",
        "consent_expires_at",
        "mobile_e164",
        "sms_status",
    ):
        op.drop_column("lead_channel_consents", name)
    op.drop_constraint("uq_communication_logs_tenant_id", "communication_logs")
    op.drop_constraint(
        "fk_lead_channel_consents_tenant_lead",
        "lead_channel_consents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_lead_channel_consents_tenant_id",
        "lead_channel_consents",
        type_="unique",
    )
    op.drop_constraint("uq_leads_tenant_id", "leads", type_="unique")
