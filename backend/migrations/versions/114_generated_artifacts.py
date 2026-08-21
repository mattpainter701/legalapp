"""Add durable generated artifacts and immutable revisions.

Revision ID: 114_generated_artifacts
Revises: 113_workspace_mcp_grants
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "114_generated_artifacts"
down_revision = "113_workspace_mcp_grants"
branch_labels = None
depends_on = None


def _enable_tenant_rls(table: str, policy: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {policy} ON {table}
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
    op.create_table(
        "generated_artifacts",
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
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "output_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matter_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("format", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column(
            "current_revision_no", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("source_channel", sa.String(40), nullable=False),
        sa.Column("client_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
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
            "client_request_id",
            name="uq_generated_artifacts_tenant_request",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_generated_artifacts_tenant_id",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'review', 'approved', 'filed', 'rejected', "
            "'superseded')",
            name="ck_generated_artifacts_status",
        ),
        sa.CheckConstraint(
            "current_revision_no > 0",
            name="ck_generated_artifacts_revision_positive",
        ),
        sa.CheckConstraint(
            "format IN ('docx', 'pdf', 'markdown')",
            name="ck_generated_artifacts_format",
        ),
        sa.CheckConstraint(
            "source_channel IN ('matter_chat', 'workspace_mcp')",
            name="ck_generated_artifacts_source_channel",
        ),
        sa.CheckConstraint(
            "length(btrim(kind)) > 0",
            name="ck_generated_artifacts_kind_nonempty",
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_artifacts_request_sha256",
        ),
    )
    op.create_index(
        "idx_generated_artifacts_tenant_matter_updated",
        "generated_artifacts",
        ["tenant_id", "matter_id", "updated_at"],
    )
    op.create_index(
        "idx_generated_artifacts_tenant_task",
        "generated_artifacts",
        ["tenant_id", "task_id"],
    )

    op.create_table(
        "generated_artifact_revisions",
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
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "parent_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("template_sha256", sa.String(64), nullable=True),
        sa.Column("template_format", sa.String(20), nullable=True),
        sa.Column("variable_snapshot", sa.JSON(), nullable=False),
        sa.Column("unresolved_variables", sa.JSON(), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("renderer_version", sa.String(80), nullable=False),
        sa.Column("model_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "artifact_id",
            "revision_no",
            name="uq_generated_artifact_revisions_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "revision_no",
            name="uq_generated_artifact_revisions_tenant_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "artifact_id",
            "id",
            name="uq_generated_artifact_revisions_tenant_artifact_id",
        ),
        sa.CheckConstraint(
            "revision_no > 0",
            name="ck_generated_artifact_revisions_number_positive",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_artifact_revisions_content_sha256",
        ),
        sa.CheckConstraint(
            "template_sha256 IS NULL OR template_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_artifact_revisions_template_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "artifact_id"],
            ["generated_artifacts.tenant_id", "generated_artifacts.id"],
            name="fk_generated_artifact_revisions_tenant_artifact",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "artifact_id", "parent_revision_id"],
            [
                "generated_artifact_revisions.tenant_id",
                "generated_artifact_revisions.artifact_id",
                "generated_artifact_revisions.id",
            ],
            name="fk_generated_artifact_revisions_parent",
            ondelete="RESTRICT",
        ),
    )
    op.create_foreign_key(
        "fk_generated_artifacts_current_revision",
        "generated_artifacts",
        "generated_artifact_revisions",
        ["tenant_id", "id", "current_revision_no"],
        ["tenant_id", "artifact_id", "revision_no"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "idx_generated_artifact_revisions_tenant_artifact",
        "generated_artifact_revisions",
        ["tenant_id", "artifact_id", "revision_no"],
    )

    _enable_tenant_rls("generated_artifacts", "generated_artifacts_tenant_isolation")
    _enable_tenant_rls(
        "generated_artifact_revisions",
        "generated_artifact_revisions_tenant_isolation",
    )
    op.execute(
        """
        CREATE FUNCTION law_hand_reject_generated_artifact_revision_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'generated_artifact_revisions is immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER generated_artifact_revisions_immutable
        BEFORE UPDATE ON generated_artifact_revisions
        FOR EACH ROW
        EXECUTE FUNCTION law_hand_reject_generated_artifact_revision_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS generated_artifact_revisions_immutable "
        "ON generated_artifact_revisions"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "law_hand_reject_generated_artifact_revision_update()"
    )
    op.drop_constraint(
        "fk_generated_artifacts_current_revision",
        "generated_artifacts",
        type_="foreignkey",
    )
    op.drop_index(
        "idx_generated_artifact_revisions_tenant_artifact",
        table_name="generated_artifact_revisions",
    )
    op.drop_table("generated_artifact_revisions")
    op.drop_index(
        "idx_generated_artifacts_tenant_task", table_name="generated_artifacts"
    )
    op.drop_index(
        "idx_generated_artifacts_tenant_matter_updated",
        table_name="generated_artifacts",
    )
    op.drop_table("generated_artifacts")
