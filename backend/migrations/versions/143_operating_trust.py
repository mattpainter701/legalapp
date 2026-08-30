"""Operating contract workflow and immutable customer-lifecycle evidence.

Revision ID: 143_operating_trust
Revises: 142_conversion_loop
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "143_operating_trust"
down_revision = "142_conversion_loop"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
        USING (
          tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
          tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )"""
    )


def upgrade() -> None:
    op.create_table(
        "customer_lifecycle_receipts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("receipt_type", sa.String(40), nullable=False),
        sa.Column("contract_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("scope_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expected_counts", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("actual_counts", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("discrepancies", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_import_run_id", UUID(as_uuid=True), sa.ForeignKey("external_import_runs.id", ondelete="RESTRICT")),
        sa.Column("artifact_reference", sa.String(1000)),
        sa.Column("artifact_sha256", sa.String(64)),
        sa.Column("signer_user_id", UUID(as_uuid=True)),
        sa.Column("signer_name", sa.String(255), nullable=False),
        sa.Column("signer_email", sa.String(320), nullable=False),
        sa.Column("signer_title", sa.String(255), nullable=False),
        sa.Column("signer_actor_type", sa.String(40), nullable=False),
        sa.Column("authority_attested", sa.Boolean(), nullable=False),
        sa.Column("approvals_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("legal_hold_snapshot", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("provider_data_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("backup_expiry_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("receipt_type IN ('onboarding', 'migration', 'tenant_export', 'offboarding', 'deletion')", name="ck_lifecycle_receipt_type"),
        sa.CheckConstraint("status IN ('accepted', 'completed', 'requested', 'blocked')", name="ck_lifecycle_receipt_status"),
        sa.CheckConstraint("receipt_hash ~ '^[0-9a-f]{64}$'", name="ck_lifecycle_receipt_hash"),
        sa.CheckConstraint("artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'", name="ck_lifecycle_artifact_hash"),
        sa.CheckConstraint("status NOT IN ('accepted', 'completed') OR authority_attested", name="ck_lifecycle_receipt_authority"),
    )
    op.create_index("ix_lifecycle_receipts_tenant_created", "customer_lifecycle_receipts", ["tenant_id", "created_at"])

    op.create_table(
        "support_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("severity", sa.String(2), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("channel", sa.String(80), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("safe_summary", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("acknowledgement_objective_minutes", sa.Integer(), nullable=False),
        sa.Column("acknowledgement_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_by_user_id", UUID(as_uuid=True)),
        sa.Column("requested_by_email", sa.String(320), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("mitigated_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("operator_actor_id", sa.String(255)),
        sa.Column("resolution_summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("severity IN ('S1', 'S2', 'S3', 'S4')", name="ck_support_severity"),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'mitigated', 'resolved')", name="ck_support_status"),
        sa.CheckConstraint("escalation_level BETWEEN 0 AND 4", name="ck_support_escalation_level"),
    )
    op.create_index("ix_support_requests_tenant_created", "support_requests", ["tenant_id", "created_at"])

    op.create_table(
        "public_incidents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("public_id", sa.String(40), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(2), nullable=False),
        sa.Column("affected_services", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_actor_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("severity IN ('S1', 'S2', 'S3')", name="ck_incident_severity"),
    )
    op.create_index("ix_public_incidents_started", "public_incidents", ["started_at"])

    op.create_table(
        "public_incident_updates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("incident_id", UUID(as_uuid=True), sa.ForeignKey("public_incidents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_by_actor_id", sa.String(255), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("state IN ('investigating', 'identified', 'monitoring', 'resolved')", name="ck_incident_update_state"),
    )
    op.create_index("ix_incident_updates_incident_published", "public_incident_updates", ["incident_id", "published_at"])

    op.create_table(
        "offboarding_cases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("requested_scope", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("legal_hold_snapshot", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("requested_by_user_id", UUID(as_uuid=True)),
        sa.Column("requested_by_email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('requested', 'hold_blocked', 'approved', 'completed')", name="ck_offboarding_status"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_offboarding_case_tenant"),
    )
    op.create_index("ix_offboarding_cases_tenant_created", "offboarding_cases", ["tenant_id", "created_at"])

    op.create_table(
        "offboarding_approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("case_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id", "tenant_id"], ["offboarding_cases.id", "offboarding_cases.tenant_id"], ondelete="RESTRICT", name="fk_offboarding_approval_case_tenant"),
        sa.UniqueConstraint("case_id", "actor_id", name="uq_offboarding_approval_actor"),
    )
    op.create_index("ix_offboarding_approvals_tenant_case", "offboarding_approvals", ["tenant_id", "case_id"])

    for table in ("customer_lifecycle_receipts", "support_requests", "offboarding_cases", "offboarding_approvals"):
        _rls(table)

    op.execute(
        """CREATE FUNCTION reject_operating_trust_ledger_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$"""
    )
    for table in ("customer_lifecycle_receipts", "offboarding_approvals", "public_incidents", "public_incident_updates"):
        op.execute(
            f"""CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_operating_trust_ledger_mutation()"""
        )


def downgrade() -> None:
    for table in ("customer_lifecycle_receipts", "offboarding_approvals", "public_incidents", "public_incident_updates"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_operating_trust_ledger_mutation()")
    for table in ("offboarding_approvals", "offboarding_cases", "support_requests", "customer_lifecycle_receipts"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_table("offboarding_approvals")
    op.drop_table("offboarding_cases")
    op.drop_table("public_incident_updates")
    op.drop_table("public_incidents")
    op.drop_table("support_requests")
    op.drop_table("customer_lifecycle_receipts")
