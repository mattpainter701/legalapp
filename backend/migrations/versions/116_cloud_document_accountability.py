"""Cloud-backed generated document bindings and storage accountability.

Revision ID: 116_cloud_doc_accountability
Revises: 115_staged_task_review
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "116_cloud_doc_accountability"
down_revision = "115_staged_task_review"
branch_labels = None
depends_on = None


def _enable_rls(table: str, policy: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {policy}
        ON {table}
        USING (
            tenant_id = NULLIF(
                current_setting('app.current_tenant_id', true), ''
            )::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.current_tenant_id', true), ''
            )::uuid
        )
        """
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    for name, column_type in (
        ("generated_artifact_id", postgresql.UUID(as_uuid=True)),
        ("generated_artifact_revision_id", postgresql.UUID(as_uuid=True)),
        ("supersedes_document_id", postgresql.UUID(as_uuid=True)),
        ("provider_etag", sa.String(500)),
        ("provider_version_id", sa.String(500)),
        ("provider_checksum", sa.String(500)),
        ("provider_modified_at", sa.DateTime(timezone=True)),
        ("storage_verified_at", sa.DateTime(timezone=True)),
        ("document_role", sa.String(40)),
        ("document_status", sa.String(30)),
        ("storage_state", sa.String(20)),
        ("document_sha256", sa.String(64)),
    ):
        op.add_column(
            "matter_documents",
            sa.Column(name, column_type, nullable=True),
        )

    op.create_unique_constraint(
        "uq_matter_documents_tenant_id",
        "matter_documents",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_matter_documents_tenant_artifact_revision",
        "matter_documents",
        ["tenant_id", "generated_artifact_revision_id"],
    )
    op.create_foreign_key(
        "fk_matter_documents_tenant_generated_artifact",
        "matter_documents",
        "generated_artifacts",
        ["tenant_id", "generated_artifact_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_matter_documents_tenant_generated_artifact_revision",
        "matter_documents",
        "generated_artifact_revisions",
        ["tenant_id", "generated_artifact_id", "generated_artifact_revision_id"],
        ["tenant_id", "artifact_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_matter_documents_tenant_supersedes",
        "matter_documents",
        "matter_documents",
        ["tenant_id", "supersedes_document_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_matter_documents_document_role",
        "matter_documents",
        "document_role IS NULL OR document_role IN "
        "('source', 'working_copy', 'review_snapshot', 'filed_copy', 'export')",
    )
    op.create_check_constraint(
        "ck_matter_documents_document_status",
        "matter_documents",
        "document_status IS NULL OR document_status IN "
        "('draft', 'in_review', 'approved', 'filed', 'superseded', 'archived')",
    )
    op.create_check_constraint(
        "ck_matter_documents_storage_state",
        "matter_documents",
        "storage_state IS NULL OR storage_state IN "
        "('untracked', 'pending', 'verified', 'conflict', 'deleted')",
    )
    op.create_check_constraint(
        "ck_matter_documents_document_sha256",
        "matter_documents",
        "document_sha256 IS NULL OR document_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_index(
        "idx_matter_documents_tenant_artifact_revision",
        "matter_documents",
        ["tenant_id", "generated_artifact_id", "generated_artifact_revision_id"],
    )
    op.create_index(
        "idx_matter_documents_tenant_storage_state",
        "matter_documents",
        ["tenant_id", "storage_state"],
    )

    op.create_table(
        "document_storage_operations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "artifact_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("operation_type", sa.String(20), nullable=False),
        sa.Column(
            "status",
            sa.String(25),
            nullable=False,
            server_default="planned",
        ),
        sa.Column("delivery_certainty", sa.String(20), nullable=True),
        sa.Column("target_provider", sa.String(50), nullable=True),
        sa.Column("target_backend", sa.String(50), nullable=True),
        sa.Column("target_drive_id", sa.String(500), nullable=True),
        sa.Column("target_parent_id", sa.String(500), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("content_size", sa.Integer(), nullable=True),
        sa.Column("provider_object_id", sa.String(500), nullable=True),
        sa.Column("provider_etag", sa.String(500), nullable=True),
        sa.Column("provider_version_id", sa.String(500), nullable=True),
        sa.Column("provider_request_id", sa.String(500), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("correlation_id", sa.String(200), nullable=True),
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
            "tenant_id",
            "id",
            name="uq_document_storage_operations_tenant_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_doc_storage_ops_tenant_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            ["generated_artifacts.tenant_id", "generated_artifacts.id"],
            name="fk_doc_storage_ops_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "artifact_revision_id"],
            [
                "generated_artifact_revisions.tenant_id",
                "generated_artifact_revisions.artifact_id",
                "generated_artifact_revisions.id",
            ],
            name="fk_doc_storage_ops_artifact_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["matter_documents.tenant_id", "matter_documents.id"],
            name="fk_doc_storage_ops_document",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "operation_type IN ('create', 'update', 'verify', 'reconcile')",
            name="ck_doc_storage_ops_type",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'writing', 'provider_accepted', 'verified', "
            "'linked', 'failed', 'ambiguous')",
            name="ck_doc_storage_ops_status",
        ),
        sa.CheckConstraint(
            "delivery_certainty IS NULL OR delivery_certainty IN "
            "('unknown', 'not_delivered', 'provider_accepted', 'verified')",
            name="ck_doc_storage_ops_certainty",
        ),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_doc_storage_ops_sha256",
        ),
        sa.CheckConstraint(
            "content_size IS NULL OR content_size >= 0",
            name="ck_doc_storage_ops_content_size",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_doc_storage_ops_attempts",
        ),
        sa.CheckConstraint(
            "length(btrim(idempotency_key)) > 0",
            name="ck_doc_storage_ops_idempotency_key",
        ),
    )
    op.create_index(
        "ix_doc_storage_ops_tenant_status",
        "document_storage_operations",
        ["tenant_id", "status", "created_at"],
    )
    op.create_index(
        "ix_doc_storage_ops_tenant_document",
        "document_storage_operations",
        ["tenant_id", "document_id", "created_at"],
    )
    _enable_rls(
        "document_storage_operations",
        "document_storage_operations_tenant_isolation",
    )

    op.create_table(
        "document_integrity_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "artifact_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_type", sa.String(30), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("provider_object_id", sa.String(500), nullable=True),
        sa.Column("provider_etag", sa.String(500), nullable=True),
        sa.Column("provider_version_id", sa.String(500), nullable=True),
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("chain_position", sa.BigInteger(), nullable=False),
        sa.Column("prev_event_hash", sa.String(64), nullable=True),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_document_integrity_events_tenant_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "event_hash",
            name="uq_document_integrity_events_tenant_hash",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "chain_position",
            name="uq_document_integrity_events_tenant_chain_position",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            ["generated_artifacts.tenant_id", "generated_artifacts.id"],
            name="fk_doc_integrity_events_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "artifact_revision_id"],
            [
                "generated_artifact_revisions.tenant_id",
                "generated_artifact_revisions.artifact_id",
                "generated_artifact_revisions.id",
            ],
            name="fk_doc_integrity_events_artifact_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["matter_documents.tenant_id", "matter_documents.id"],
            name="fk_doc_integrity_events_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "operation_id"],
            [
                "document_storage_operations.tenant_id",
                "document_storage_operations.id",
            ],
            name="fk_doc_integrity_events_operation",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(btrim(event_type)) > 0",
            name="ck_doc_integrity_events_type",
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service', 'provider', 'system')",
            name="ck_doc_integrity_events_actor_type",
        ),
        sa.CheckConstraint(
            "chain_position > 0",
            name="ck_doc_integrity_events_chain_position",
        ),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_doc_integrity_events_sha256",
        ),
        sa.CheckConstraint(
            "prev_event_hash IS NULL OR prev_event_hash ~ '^[0-9a-f]{64}$'",
            name="ck_doc_integrity_events_prev_hash",
        ),
        sa.CheckConstraint(
            "event_hash ~ '^[0-9a-f]{64}$'",
            name="ck_doc_integrity_events_hash",
        ),
    )
    op.create_index(
        "ix_doc_integrity_events_tenant_document_created",
        "document_integrity_events",
        ["tenant_id", "document_id", "created_at"],
    )
    op.create_index(
        "ix_doc_integrity_events_tenant_created",
        "document_integrity_events",
        ["tenant_id", "created_at", "id"],
    )
    _enable_rls(
        "document_integrity_events",
        "document_integrity_events_tenant_isolation",
    )
    op.execute(
        """
        CREATE FUNCTION law_hand_reject_document_integrity_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'document_integrity_events is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_integrity_events_append_only
        BEFORE UPDATE OR DELETE ON document_integrity_events
        FOR EACH ROW
        EXECUTE FUNCTION law_hand_reject_document_integrity_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS document_integrity_events_append_only "
        "ON document_integrity_events"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS " "law_hand_reject_document_integrity_event_mutation()"
    )
    op.execute(
        "DROP POLICY IF EXISTS document_integrity_events_tenant_isolation "
        "ON document_integrity_events"
    )
    op.drop_table("document_integrity_events")

    op.execute(
        "DROP POLICY IF EXISTS document_storage_operations_tenant_isolation "
        "ON document_storage_operations"
    )
    op.drop_table("document_storage_operations")

    op.drop_index(
        "idx_matter_documents_tenant_storage_state",
        table_name="matter_documents",
    )
    op.drop_index(
        "idx_matter_documents_tenant_artifact_revision",
        table_name="matter_documents",
    )
    for name in (
        "ck_matter_documents_document_sha256",
        "ck_matter_documents_storage_state",
        "ck_matter_documents_document_status",
        "ck_matter_documents_document_role",
    ):
        op.drop_constraint(name, "matter_documents", type_="check")
    op.drop_constraint(
        "fk_matter_documents_tenant_supersedes",
        "matter_documents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_matter_documents_tenant_generated_artifact_revision",
        "matter_documents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_matter_documents_tenant_generated_artifact",
        "matter_documents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_matter_documents_tenant_artifact_revision",
        "matter_documents",
        type_="unique",
    )
    op.drop_constraint(
        "uq_matter_documents_tenant_id",
        "matter_documents",
        type_="unique",
    )
    for name in (
        "document_sha256",
        "storage_state",
        "document_status",
        "document_role",
        "storage_verified_at",
        "provider_modified_at",
        "provider_checksum",
        "provider_version_id",
        "provider_etag",
        "supersedes_document_id",
        "generated_artifact_revision_id",
        "generated_artifact_id",
    ):
        op.drop_column("matter_documents", name)
