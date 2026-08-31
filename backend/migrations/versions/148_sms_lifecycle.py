"""Tenant-scoped provider-backed SMS lifecycle and consent provenance."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "148_sms_lifecycle"
down_revision = "147_studio_drafts"
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
        sa.Column("encrypted_webhook_secret", sa.Text()),
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
        sa.Column("provider_message_id", sa.String(255)),
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
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_sms_messages_tenant_idempotency"
        ),
        sa.UniqueConstraint(
            "tenant_id", "provider_message_id", name="uq_sms_messages_provider_id"
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
    )
    for table in ("sms_provider_configs", "sms_messages", "sms_review_items"):
        _rls(table)


def downgrade() -> None:
    for table in ("sms_review_items", "sms_messages", "sms_provider_configs"):
        op.drop_table(table)
    for name in (
        "allowed_categories",
        "quiet_hours_end",
        "quiet_hours_start",
        "consent_timezone",
        "consent_language",
        "consent_source",
        "consented_at",
        "mobile_e164",
        "sms_status",
    ):
        op.drop_column("lead_channel_consents", name)
