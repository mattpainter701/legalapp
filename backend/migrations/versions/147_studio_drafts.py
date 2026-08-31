"""Add revision-safe, tenant-isolated Template Studio drafts.

Revision ID: 147_studio_drafts
Revises: 146_research_workspaces
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "147_studio_drafts"
down_revision = "146_research_workspaces"
branch_labels = None
depends_on = None


TABLES = (
    "studio_source_artifacts",
    "studio_drafts",
    "studio_draft_fields",
    "studio_draft_placements",
    "studio_draft_snapshots",
    "studio_draft_idempotency",
    "studio_draft_audit_events",
)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    u = postgresql.UUID(as_uuid=True)
    op.create_table(
        "studio_source_artifacts",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("created_by_user_id", u),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "sha256",
            "media_type",
            name="uq_studio_source_artifacts_contract",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name="ck_studio_source_artifacts_hash"
        ),
    )
    op.create_index(
        "ix_studio_source_artifacts_tenant_created",
        "studio_source_artifacts",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "studio_drafts",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("published_template_id", u),
        sa.Column("source_artifact_id", u, nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_media_type", sa.String(100), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("format", sa.String(30), nullable=False),
        sa.Column(
            "lifecycle_state", sa.String(20), server_default="active", nullable=False
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("identity_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_revision", sa.Integer()),
        sa.Column("evidence_invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("evidence_invalidation_reason", sa.String(60)),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", u),
        sa.Column("updated_by_user_id", u),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_artifact_id", "source_sha256", "source_media_type"],
            [
                "studio_source_artifacts.tenant_id",
                "studio_source_artifacts.id",
                "studio_source_artifacts.sha256",
                "studio_source_artifacts.media_type",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["published_template_id"], ["document_templates.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_studio_drafts_tenant_id"),
        sa.CheckConstraint("revision > 0", name="ck_studio_drafts_revision"),
        sa.CheckConstraint(
            "lifecycle_state IN ('active', 'archived')",
            name="ck_studio_drafts_lifecycle",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' AND identity_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_studio_drafts_hashes",
        ),
    )
    op.create_index(
        "ix_studio_drafts_tenant_updated", "studio_drafts", ["tenant_id", "updated_at"]
    )

    op.create_table(
        "studio_draft_fields",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("draft_id", u, nullable=False),
        sa.Column("automation_key", sa.String(120), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("field_type", sa.String(40), nullable=False),
        sa.Column("required", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "definition",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "draft_id", "id", name="uq_studio_fields_scope"
        ),
        sa.UniqueConstraint("draft_id", "automation_key", name="uq_studio_fields_key"),
        sa.CheckConstraint("position >= 0", name="ck_studio_fields_position"),
    )
    op.create_index(
        "ix_studio_fields_draft_position",
        "studio_draft_fields",
        ["tenant_id", "draft_id", "position"],
    )

    op.create_table(
        "studio_draft_placements",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("draft_id", u, nullable=False),
        sa.Column("field_id", u, nullable=False),
        sa.Column("format", sa.String(30), nullable=False),
        sa.Column("anchor_kind", sa.String(40), nullable=False),
        sa.Column("anchor", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id", "field_id"],
            [
                "studio_draft_fields.tenant_id",
                "studio_draft_fields.draft_id",
                "studio_draft_fields.id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "draft_id", "id", name="uq_studio_placements_scope"
        ),
    )
    op.create_index(
        "ix_studio_placements_field",
        "studio_draft_placements",
        ["tenant_id", "draft_id", "field_id"],
    )

    op.create_table(
        "studio_draft_snapshots",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("draft_id", u, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("identity_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", u),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_id", "revision", name="uq_studio_snapshots_revision"
        ),
        sa.UniqueConstraint(
            "draft_id", "content_sha256", name="uq_studio_snapshots_content"
        ),
        sa.CheckConstraint("revision > 0", name="ck_studio_snapshots_revision"),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_studio_snapshots_hash"
        ),
    )
    op.create_index(
        "ix_studio_snapshots_draft_created",
        "studio_draft_snapshots",
        ["tenant_id", "draft_id", "created_at"],
    )

    op.create_table(
        "studio_draft_idempotency",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("actor_user_id", u, nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("response_json", sa.JSON()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "operation",
            "idempotency_key",
            name="uq_studio_idempotency_key",
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'", name="ck_studio_idempotency_hash"
        ),
    )
    op.create_index(
        "ix_studio_idempotency_expires",
        "studio_draft_idempotency",
        ["tenant_id", "expires_at"],
    )

    op.create_table(
        "studio_draft_audit_events",
        sa.Column("id", u, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", u, nullable=False),
        sa.Column("draft_id", u, nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("base_revision", sa.Integer()),
        sa.Column("actor_user_id", u),
        sa.Column(
            "detail", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_id"],
            ["studio_drafts.tenant_id", "studio_drafts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_studio_audit_draft_created",
        "studio_draft_audit_events",
        ["tenant_id", "draft_id", "created_at"],
    )

    for table in TABLES:
        _rls(table)

    op.execute("""
        CREATE FUNCTION validate_studio_optional_users() RETURNS trigger AS $$
        DECLARE actor uuid;
        BEGIN
            IF TG_TABLE_NAME = 'studio_source_artifacts' THEN
                actor := NEW.created_by_user_id;
            ELSIF TG_TABLE_NAME = 'studio_drafts' THEN
                IF NEW.published_template_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM document_templates
                    WHERE id = NEW.published_template_id AND tenant_id = NEW.tenant_id
                ) THEN RAISE EXCEPTION 'studio published template tenant mismatch'; END IF;
                IF NEW.created_by_user_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM users WHERE id = NEW.created_by_user_id AND tenant_id = NEW.tenant_id
                ) THEN RAISE EXCEPTION 'studio creator tenant mismatch'; END IF;
                actor := NEW.updated_by_user_id;
            ELSIF TG_TABLE_NAME = 'studio_draft_snapshots' THEN
                actor := NEW.created_by_user_id;
            ELSE
                actor := NEW.actor_user_id;
            END IF;
            IF actor IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM users WHERE id = actor AND tenant_id = NEW.tenant_id
            ) THEN RAISE EXCEPTION 'studio actor tenant mismatch'; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in (
        "studio_source_artifacts",
        "studio_drafts",
        "studio_draft_snapshots",
        "studio_draft_audit_events",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_user_tenant_trigger BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION validate_studio_optional_users()"
        )

    op.execute("""
        CREATE FUNCTION guard_studio_source_identity() RETURNS trigger AS $$
        BEGIN
            IF NEW.source_artifact_id = OLD.source_artifact_id
               AND (NEW.source_sha256 <> OLD.source_sha256 OR NEW.source_media_type <> OLD.source_media_type)
            THEN RAISE EXCEPTION 'studio source artifact identity is immutable'; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER studio_drafts_source_identity_guard BEFORE UPDATE OF source_artifact_id, source_sha256, source_media_type ON studio_drafts FOR EACH ROW EXECUTE FUNCTION guard_studio_source_identity()"
    )

    op.execute("""
        CREATE FUNCTION prevent_studio_immutable_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'studio snapshots and audit events are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in (
        "studio_source_artifacts",
        "studio_draft_snapshots",
        "studio_draft_audit_events",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_studio_immutable_mutation()"
        )


def downgrade() -> None:
    for table in (
        "studio_draft_audit_events",
        "studio_draft_snapshots",
        "studio_source_artifacts",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute(
        "DROP TRIGGER IF EXISTS studio_drafts_source_identity_guard ON studio_drafts"
    )
    for table in (
        "studio_draft_audit_events",
        "studio_draft_snapshots",
        "studio_drafts",
        "studio_source_artifacts",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_user_tenant_trigger ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_studio_immutable_mutation()")
    op.execute("DROP FUNCTION IF EXISTS guard_studio_source_identity()")
    op.execute("DROP FUNCTION IF EXISTS validate_studio_optional_users()")
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
