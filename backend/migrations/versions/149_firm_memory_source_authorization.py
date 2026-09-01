"""Firm Memory source catalog and fail-closed authorization policy.

Revision ID: 149_firm_memory_source_auth
Revises: 148_configurable_workflows
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "149_firm_memory_source_auth"
down_revision = "148_configurable_workflows"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"""
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    u = postgresql.UUID(as_uuid=True)
    op.create_unique_constraint(
        "uq_smb_shares_tenant_id", "smb_shares", ["tenant_id", "id"]
    )

    op.create_table(
        "firm_memory_sources",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("source_key", sa.String(120), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("provider_key", sa.String(120)),
        sa.Column(
            "authorization_mode",
            sa.String(20),
            server_default="explicit",
            nullable=False,
        ),
        sa.Column("native_authorizer_key", sa.String(120)),
        sa.Column("legacy_smb_share_id", u),
        sa.Column(
            "coverage_state",
            sa.String(20),
            server_default="unsupported",
            nullable=False,
        ),
        sa.Column(
            "is_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_firm_memory_sources_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_key", name="uq_firm_memory_sources_key"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "legacy_smb_share_id"],
            ["smb_shares.tenant_id", "smb_shares.id"],
            name="fk_firm_memory_sources_legacy_smb_share",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "btrim(source_key) <> '' AND btrim(display_name) <> ''",
            name="ck_firm_memory_sources_names",
        ),
        sa.CheckConstraint(
            "source_kind IN ('smb','cloud','matter_documents','native')",
            name="ck_firm_memory_sources_kind",
        ),
        sa.CheckConstraint(
            "authorization_mode IN ('firm','matter','explicit','native')",
            name="ck_firm_memory_sources_authorization_mode",
        ),
        sa.CheckConstraint(
            "coverage_state IN ('ready','partial','indexing','stale','offline','unsupported')",
            name="ck_firm_memory_sources_coverage_state",
        ),
        sa.CheckConstraint(
            "source_kind = 'smb' OR legacy_smb_share_id IS NULL",
            name="ck_firm_memory_sources_legacy_share_kind",
        ),
        sa.CheckConstraint(
            "authorization_mode <> 'native' OR native_authorizer_key IS NOT NULL",
            name="ck_firm_memory_sources_native_authorizer",
        ),
    )
    op.create_index(
        "ix_firm_memory_sources_tenant_kind",
        "firm_memory_sources",
        ["tenant_id", "source_kind"],
    )

    op.create_table(
        "firm_memory_collections",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("collection_key", sa.String(120), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "is_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_firm_memory_collections_tenant_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "collection_key", name="uq_firm_memory_collections_key"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "btrim(collection_key) <> '' AND btrim(display_name) <> ''",
            name="ck_firm_memory_collections_names",
        ),
    )
    op.create_index(
        "ix_firm_memory_collections_tenant", "firm_memory_collections", ["tenant_id"]
    )

    op.create_table(
        "firm_memory_collection_sources",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("collection_id", u, nullable=False),
        sa.Column("source_id", u, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "collection_id",
            "source_id",
            name="uq_firm_memory_collection_source",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["firm_memory_collections.tenant_id", "firm_memory_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["firm_memory_sources.tenant_id", "firm_memory_sources.id"],
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "firm_memory_source_grants",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("source_id", u, nullable=False),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", u, nullable=False),
        sa.Column("effect", sa.String(10), server_default="deny", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "subject_type",
            "subject_id",
            name="uq_firm_memory_source_grant_subject",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["firm_memory_sources.tenant_id", "firm_memory_sources.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "subject_type IN ('user','role')",
            name="ck_firm_memory_source_grants_subject",
        ),
        sa.CheckConstraint(
            "effect IN ('allow','deny')", name="ck_firm_memory_source_grants_effect"
        ),
    )
    op.create_index(
        "ix_firm_memory_source_grants_subject",
        "firm_memory_source_grants",
        ["tenant_id", "subject_type", "subject_id"],
    )

    op.create_table(
        "firm_memory_matter_policies",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("matter_id", u, nullable=False),
        sa.Column(
            "access_mode", sa.String(20), server_default="restricted", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "matter_id", name="uq_firm_memory_matter_policy"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "access_mode IN ('firm','assigned','restricted')",
            name="ck_firm_memory_matter_policies_mode",
        ),
    )

    op.create_table(
        "firm_memory_matter_grants",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("matter_id", u, nullable=False),
        sa.Column("user_id", u, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "matter_id", "user_id", name="uq_firm_memory_matter_grant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "firm_memory_document_matters",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("source_id", u, nullable=False),
        sa.Column("document_key", sa.String(500), nullable=False),
        sa.Column("matter_id", u, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "document_key",
            "matter_id",
            name="uq_firm_memory_document_matter",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["firm_memory_sources.tenant_id", "firm_memory_sources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_firm_memory_document_matters_document",
        "firm_memory_document_matters",
        ["tenant_id", "source_id", "document_key"],
    )

    op.create_table(
        "firm_memory_document_workspaces",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("source_id", u, nullable=False),
        sa.Column("document_key", sa.String(500), nullable=False),
        sa.Column("workspace_id", u, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "document_key",
            "workspace_id",
            name="uq_firm_memory_document_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["firm_memory_sources.tenant_id", "firm_memory_sources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["research_workspaces.tenant_id", "research_workspaces.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_firm_memory_document_workspaces_document",
        "firm_memory_document_workspaces",
        ["tenant_id", "source_id", "document_key"],
    )

    for table in (
        "firm_memory_sources",
        "firm_memory_collections",
        "firm_memory_collection_sources",
        "firm_memory_source_grants",
        "firm_memory_matter_policies",
        "firm_memory_matter_grants",
        "firm_memory_document_matters",
        "firm_memory_document_workspaces",
    ):
        _rls(table)

    # Existing administrators retain operability.  Other roles require an
    # explicit firm decision before generalized research is available.
    op.execute(
        "UPDATE roles SET capabilities = capabilities || '[\"search_firm_memory\"]'::jsonb, "
        "updated_at = now() WHERE name = 'Administrator' AND is_system IS TRUE "
        "AND NOT (capabilities @> '[\"search_firm_memory\"]'::jsonb)"
    )


def downgrade() -> None:
    # Deliberately retain a capability that may have been intentionally granted
    # after upgrade; there is no provenance that makes revocation safe.
    for table in (
        "firm_memory_document_workspaces",
        "firm_memory_document_matters",
        "firm_memory_matter_grants",
        "firm_memory_matter_policies",
        "firm_memory_source_grants",
        "firm_memory_collection_sources",
        "firm_memory_collections",
        "firm_memory_sources",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
    op.drop_constraint("uq_smb_shares_tenant_id", "smb_shares", type_="unique")
