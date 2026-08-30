"""Immutable agreement ledger and tenant retention controls.

Revision ID: 139_agreements_retention
Revises: 138_research_key_controls

No legal documents are seeded. An operator must publish counsel-approved,
content-addressed definitions before the optional onboarding gate is enabled.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "139_agreements_retention"
down_revision = "138_research_key_controls"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
        USING (
          tenant_id = NULLIF(
            current_setting('app.current_tenant_id', true), ''
          )::uuid
        )
        WITH CHECK (
          tenant_id = NULLIF(
            current_setting('app.current_tenant_id', true), ''
          )::uuid
        )"""
    )


def upgrade() -> None:
    op.create_table(
        "agreement_definitions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("document_url", sa.String(2000), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "required_for_onboarding",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("counsel_owned", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("published_by_actor_id", sa.String(255), nullable=False),
        sa.Column(
            "metadata_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$' " "AND content_hash <> repeat('0', 64)",
            name="ck_agreement_definition_content_hash",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_at",
            name="ck_agreement_definition_window",
        ),
        sa.UniqueConstraint("kind", "version", name="uq_agreement_kind_version"),
    )
    op.create_index("ix_agreement_definitions_kind", "agreement_definitions", ["kind"])
    op.create_index(
        "ix_agreement_definitions_effective",
        "agreement_definitions",
        ["required_for_onboarding", "effective_at"],
    )

    op.create_table(
        "tenant_agreement_acceptances",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "agreement_definition_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agreement_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tenant_name", sa.String(255), nullable=False),
        sa.Column("document_kind", sa.String(80), nullable=False),
        sa.Column("document_version", sa.String(40), nullable=False),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("document_url", sa.String(2000), nullable=False),
        sa.Column(
            "signer_user_id",
            UUID(as_uuid=True),
        ),
        sa.Column("signer_name", sa.String(255), nullable=False),
        sa.Column("signer_email", sa.String(320), nullable=False),
        sa.Column("signer_title", sa.String(255), nullable=False),
        sa.Column("authority_attested", sa.Boolean(), nullable=False),
        sa.Column("attestation_text", sa.Text(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("auth_method", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="accepted"),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("esign_provider", sa.String(80)),
        sa.Column("esign_envelope_id", sa.String(255)),
        sa.Column("evidence_reference", sa.String(2000)),
        sa.Column(
            "metadata_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "document_hash ~ '^[0-9a-f]{64}$'",
            name="ck_tenant_acceptance_document_hash",
        ),
        sa.CheckConstraint(
            "status <> 'accepted' OR authority_attested",
            name="ck_tenant_acceptance_authority",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "agreement_definition_id",
            name="uq_tenant_agreement_acceptance",
        ),
    )
    op.create_index(
        "ix_tenant_agreement_acceptances_tenant",
        "tenant_agreement_acceptances",
        ["tenant_id"],
    )

    op.create_table(
        "retention_policies",
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
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("legal_hold_reason", sa.Text()),
        sa.Column("legal_hold_set_at", sa.DateTime(timezone=True)),
        sa.Column(
            "policy_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("version >= 1", name="ck_retention_policy_version"),
        sa.CheckConstraint(
            "NOT legal_hold OR legal_hold_reason IS NOT NULL",
            name="ck_retention_policy_legal_hold_reason",
        ),
        sa.UniqueConstraint("tenant_id", name="uq_retention_policy_tenant"),
    )

    op.create_table(
        "retention_actions",
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
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("actor_type", sa.String(30), nullable=False, server_default="user"),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="completed"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("legal_hold_at_execution", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.Integer()),
        sa.Column(
            "result_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_retention_actions_tenant_created",
        "retention_actions",
        ["tenant_id", "created_at"],
    )

    for table in (
        "tenant_agreement_acceptances",
        "retention_policies",
        "retention_actions",
    ):
        _rls(table)

    op.execute(
        """CREATE FUNCTION reject_compliance_ledger_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$"""
    )
    for table in ("agreement_definitions", "tenant_agreement_acceptances"):
        op.execute(
            f"""CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_compliance_ledger_mutation()"""
        )


def downgrade() -> None:
    for table in ("agreement_definitions", "tenant_agreement_acceptances"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_compliance_ledger_mutation()")
    for table in (
        "retention_actions",
        "retention_policies",
        "tenant_agreement_acceptances",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_table("retention_actions")
    op.drop_table("retention_policies")
    op.drop_table("tenant_agreement_acceptances")
    op.drop_table("agreement_definitions")
