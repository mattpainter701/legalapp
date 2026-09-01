"""Add tenant-isolated durable Template Studio render evidence.

Revision ID: 149_studio_render_jobs
Revises: 148_configurable_workflows
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "149_studio_render_jobs"
down_revision = "148_configurable_workflows"
branch_labels = None
depends_on = None


STUDIO_KINDS_SQL = (
    "'studio_template_analysis', 'studio_template_ocr', "
    "'studio_page_preview', 'studio_test_render'"
)


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = "
        "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = "
        "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    u = postgresql.UUID(as_uuid=True)

    op.create_unique_constraint(
        "uq_durable_jobs_tenant_id", "durable_jobs", ["tenant_id", "id"]
    )
    op.create_unique_constraint(
        "uq_studio_snapshots_tenant_id",
        "studio_draft_snapshots",
        ["tenant_id", "id"],
    )
    op.create_index(
        "uq_durable_jobs_studio_idempotency",
        "durable_jobs",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text(f"kind IN ({STUDIO_KINDS_SQL})"),
    )
    op.create_index(
        "ix_durable_jobs_studio_claim",
        "durable_jobs",
        ["tenant_id", "status", "available_at", "created_at"],
        postgresql_where=sa.text(
            f"kind IN ({STUDIO_KINDS_SQL}) "
            "AND status IN ('pending', 'running', 'cancel_requested')"
        ),
    )

    op.create_table(
        "studio_render_artifacts",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("job_id", u, nullable=False),
        sa.Column("draft_id", u, nullable=False),
        sa.Column("snapshot_id", u, nullable=False),
        sa.Column("source_artifact_id", u, nullable=False),
        sa.Column("requested_by_user_id", u, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("identity_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_basis_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_content_sha256", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_media_type", sa.String(100), nullable=False),
        sa.Column("source_format", sa.String(20), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("effective_request_sha256", sa.String(64), nullable=False),
        sa.Column("render_options", sa.JSON(), nullable=False),
        sa.Column("render_options_sha256", sa.String(64), nullable=False),
        sa.Column("requested_page_number", sa.Integer()),
        sa.Column("requested_page_range_start", sa.Integer()),
        sa.Column("requested_page_range_end", sa.Integer()),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("artifact_kind", sa.String(30), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False),
        sa.Column("runtime_manifest", sa.JSON(), nullable=False),
        sa.Column("runtime_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("input_binding_sha256", sa.String(64)),
        sa.Column("input_binding_version", sa.Integer()),
        sa.Column("artifact_page_count", sa.Integer(), nullable=False),
        sa.Column("document_page_count", sa.Integer(), nullable=False),
        sa.Column("geometry_manifest", sa.JSON(), nullable=False),
        sa.Column("geometry_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("adoption_outcome", sa.String(30), nullable=False),
        sa.Column(
            "retention_class",
            sa.String(20),
            server_default="ephemeral",
            nullable=False,
        ),
        sa.Column(
            "storage_state", sa.String(20), server_default="active", nullable=False
        ),
        sa.Column("content_expires_at", sa.DateTime(timezone=True)),
        sa.Column("metadata_expires_at", sa.DateTime(timezone=True)),
        sa.Column("legal_hold_at", sa.DateTime(timezone=True)),
        sa.Column("delete_requested_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["durable_jobs.tenant_id", "durable_jobs.id"],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_job_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_draft_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["studio_draft_snapshots.tenant_id", "studio_draft_snapshots.id"],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_snapshot_tenant",
        ),
        sa.ForeignKeyConstraint(
            [
                "tenant_id",
                "source_artifact_id",
                "source_sha256",
                "source_media_type",
                "source_format",
            ],
            [
                "studio_source_artifacts.tenant_id",
                "studio_source_artifacts.id",
                "studio_source_artifacts.sha256",
                "studio_source_artifacts.media_type",
                "studio_source_artifacts.format",
            ],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_source_contract",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_studio_render_artifact_requester_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_studio_render_artifact_tenant_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "job_id", name="uq_studio_render_artifact_job"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "job_id",
            "draft_id",
            "revision",
            "identity_sha256",
            "evidence_basis_sha256",
            name="uq_studio_render_artifact_evidence",
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' "
            "AND effective_request_sha256 ~ '^[0-9a-f]{64}$' "
            "AND render_options_sha256 ~ '^[0-9a-f]{64}$' "
            "AND cache_key ~ '^[0-9a-f]{64}$' "
            "AND content_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_sha256 ~ '^[0-9a-f]{64}$' "
            "AND identity_sha256 ~ '^[0-9a-f]{64}$' "
            "AND snapshot_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_studio_render_artifact_hashes",
        ),
        sa.CheckConstraint(
            "runtime_manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND geometry_manifest_sha256 ~ '^[0-9a-f]{64}$' "
            "AND evidence_basis_sha256 ~ '^[0-9a-f]{64}$' "
            "AND (input_binding_sha256 IS NULL OR "
            "input_binding_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_studio_render_artifact_manifest_hashes",
        ),
        sa.CheckConstraint(
            "(input_binding_sha256 IS NULL) = (input_binding_version IS NULL)",
            name="ck_studio_render_artifact_input_binding",
        ),
        sa.CheckConstraint("revision > 0", name="ck_studio_render_artifact_revision"),
        sa.CheckConstraint("byte_size > 0", name="ck_studio_render_artifact_size"),
        sa.CheckConstraint(
            "artifact_page_count > 0 "
            "AND document_page_count >= artifact_page_count",
            name="ck_studio_render_artifact_pages",
        ),
        sa.CheckConstraint(
            "(requested_page_range_start IS NULL) = "
            "(requested_page_range_end IS NULL) "
            "AND (requested_page_range_start IS NULL OR "
            "(requested_page_range_start > 0 AND requested_page_range_end >= "
            "requested_page_range_start))",
            name="ck_studio_render_artifact_requested_range",
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('analysis', 'ocr', 'page_preview', 'test_render')",
            name="ck_studio_render_artifact_kind",
        ),
        sa.CheckConstraint(
            "adoption_outcome IN "
            "('current_evidence', 'stale_output', 'cancelled_output')",
            name="ck_studio_render_adoption_outcome",
        ),
        sa.CheckConstraint(
            "retention_class IN ('ephemeral', 'review', 'evidence')",
            name="ck_studio_render_retention_class",
        ),
        sa.CheckConstraint(
            "storage_state IN ('active', 'delete_pending', 'deleted')",
            name="ck_studio_render_storage_state",
        ),
        sa.CheckConstraint(
            "(retention_class = 'evidence' AND content_expires_at IS NULL "
            "AND metadata_expires_at IS NULL) OR "
            "(retention_class IN ('ephemeral', 'review') "
            "AND content_expires_at IS NOT NULL "
            "AND metadata_expires_at IS NOT NULL "
            "AND metadata_expires_at > content_expires_at)",
            name="ck_studio_render_artifact_temporary_expiry",
        ),
        sa.CheckConstraint(
            "(artifact_kind = 'page_preview' "
            "AND requested_page_number IS NOT NULL AND artifact_page_count = 1) OR "
            "(artifact_kind != 'page_preview' AND requested_page_number IS NULL)",
            name="ck_studio_render_artifact_preview_page",
        ),
        sa.CheckConstraint(
            "(storage_state = 'active' AND delete_requested_at IS NULL "
            "AND deleted_at IS NULL) OR "
            "(storage_state = 'delete_pending' AND delete_requested_at IS NOT NULL "
            "AND deleted_at IS NULL) OR "
            "(storage_state = 'deleted' AND delete_requested_at IS NOT NULL "
            "AND deleted_at IS NOT NULL)",
            name="ck_studio_render_storage_lifecycle",
        ),
    )
    op.create_index(
        "ix_studio_render_cache",
        "studio_render_artifacts",
        ["tenant_id", "cache_key", "created_at"],
    )
    op.create_index(
        "ix_studio_render_cleanup",
        "studio_render_artifacts",
        ["tenant_id", "storage_state", "retention_class", "content_expires_at"],
    )
    op.create_index(
        "ix_studio_render_draft_revision",
        "studio_render_artifacts",
        ["tenant_id", "draft_id", "revision"],
    )
    op.create_index(
        "ix_studio_render_object_state",
        "studio_render_artifacts",
        ["tenant_id", "object_key", "storage_state"],
    )

    op.create_table(
        "studio_preferred_render_evidence",
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("draft_id", u, nullable=False),
        sa.Column("evidence_basis_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_id", u, nullable=False),
        sa.Column("job_id", u, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("identity_sha256", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="CASCADE",
            name="fk_studio_preferred_render_draft_tenant",
        ),
        sa.ForeignKeyConstraint(
            [
                "tenant_id",
                "artifact_id",
                "job_id",
                "draft_id",
                "revision",
                "identity_sha256",
                "evidence_basis_sha256",
            ],
            [
                "studio_render_artifacts.tenant_id",
                "studio_render_artifacts.id",
                "studio_render_artifacts.job_id",
                "studio_render_artifacts.draft_id",
                "studio_render_artifacts.revision",
                "studio_render_artifacts.identity_sha256",
                "studio_render_artifacts.evidence_basis_sha256",
            ],
            ondelete="RESTRICT",
            name="fk_studio_preferred_render_exact_evidence",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "draft_id", "evidence_basis_sha256"),
        sa.UniqueConstraint(
            "tenant_id", "artifact_id", name="uq_studio_preferred_render_artifact"
        ),
        sa.CheckConstraint(
            "revision > 0", name="ck_studio_preferred_render_revision"
        ),
        sa.CheckConstraint(
            "identity_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_studio_preferred_render_identity",
        ),
        sa.CheckConstraint(
            "evidence_basis_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_studio_preferred_render_basis",
        ),
    )

    op.execute(
        """
        CREATE FUNCTION guard_studio_render_artifact_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF current_setting(
                       'app.studio_demo_purge_tenant_id', true
                   ) = OLD.tenant_id::text
                   AND EXISTS (
                       SELECT 1
                       FROM tenants tenant
                       JOIN demo_sessions demo ON demo.tenant_id = tenant.id
                       WHERE tenant.id = OLD.tenant_id
                         AND tenant.billing_tier = 'demo'
                         AND tenant.domain LIKE '%.demo.invalid'
                         AND tenant.is_active = false
                         AND tenant.expires_at <= now()
                         AND demo.id::text = current_setting(
                             'app.studio_demo_purge_session_id', true
                         )
                         AND demo.status = 'purging'
                         AND demo.fixture_tenant_id <> demo.tenant_id
                         AND demo.purge_started_at IS NOT NULL
                   ) THEN
                    RETURN OLD;
                END IF;
                IF OLD.storage_state != 'deleted'
                   OR OLD.legal_hold_at IS NOT NULL
                   OR OLD.metadata_expires_at IS NULL
                   OR OLD.metadata_expires_at > clock_timestamp()
                   OR EXISTS (
                       SELECT 1
                       FROM studio_preferred_render_evidence preferred
                       WHERE preferred.tenant_id = OLD.tenant_id
                         AND preferred.artifact_id = OLD.id
                         AND preferred.job_id = OLD.job_id
                   ) THEN
                    RAISE EXCEPTION 'Studio render evidence is retained';
                END IF;
                RETURN OLD;
            END IF;
            IF (
                to_jsonb(NEW) - ARRAY[
                    'retention_class', 'content_expires_at', 'metadata_expires_at',
                    'legal_hold_at', 'storage_state', 'delete_requested_at', 'deleted_at'
                ]
            ) IS DISTINCT FROM (
                to_jsonb(OLD) - ARRAY[
                    'retention_class', 'content_expires_at', 'metadata_expires_at',
                    'legal_hold_at', 'storage_state', 'delete_requested_at', 'deleted_at'
                ]
            ) THEN
                RAISE EXCEPTION 'Studio render evidence is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER studio_render_artifact_evidence_immutable
        BEFORE UPDATE OR DELETE ON studio_render_artifacts
        FOR EACH ROW EXECUTE FUNCTION guard_studio_render_artifact_evidence()
        """
    )

    _enable_rls("studio_render_artifacts")
    _enable_rls("studio_preferred_render_evidence")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS studio_preferred_render_evidence_tenant_isolation "
        "ON studio_preferred_render_evidence"
    )
    op.drop_table("studio_preferred_render_evidence")

    op.execute(
        "DROP TRIGGER IF EXISTS studio_render_artifact_evidence_immutable "
        "ON studio_render_artifacts"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_studio_render_artifact_evidence()")
    op.execute(
        "DROP POLICY IF EXISTS studio_render_artifacts_tenant_isolation "
        "ON studio_render_artifacts"
    )
    op.drop_table("studio_render_artifacts")

    op.drop_index("ix_durable_jobs_studio_claim", table_name="durable_jobs")
    op.drop_index("uq_durable_jobs_studio_idempotency", table_name="durable_jobs")
    op.drop_constraint(
        "uq_studio_snapshots_tenant_id",
        "studio_draft_snapshots",
        type_="unique",
    )
    op.drop_constraint(
        "uq_durable_jobs_tenant_id", "durable_jobs", type_="unique"
    )
