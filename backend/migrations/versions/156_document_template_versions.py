"""Record an immutable version each time a document template is published.

``document_templates`` had no version column and PATCH overwrote in place, so a
firm could not see what changed, could not roll back, and could not tell which
wording produced a document that has since been filed or signed. Generated
output already stored the template SHA-256 it came from, which proves *which
bytes* rendered a document but cannot show a human what that template said.

Rows are append-only to ordinary application transactions, matching the posture
of the existing Studio tables: a version is evidence about a document that may
have left the firm, so it must not be editable after the fact. Deleting the
template still cascades, because a deleted template's history has no subject.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "156_document_template_versions"
down_revision = "155_matter_workflow_automations"
branch_labels = None
depends_on = None


TENANT_PREDICATE = (
    "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "document_template_versions",
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
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("variable_schema", sa.JSON(), nullable=True),
        sa.Column("format", sa.String(50), nullable=True),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("source_filename", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("change_summary", sa.String(500), nullable=True),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
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
            "tenant_id", "template_id", "version_no",
            name="uq_document_template_versions_number",
        ),
        sa.CheckConstraint(
            "version_no >= 1", name="ck_document_template_versions_positive"
        ),
    )
    op.create_index(
        "ix_document_template_versions_tenant_template",
        "document_template_versions",
        ["tenant_id", "template_id", "version_no"],
    )
    op.execute("ALTER TABLE document_template_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_template_versions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY document_template_versions_tenant_isolation
        ON document_template_versions
        USING ({TENANT_PREDICATE})
        WITH CHECK ({TENANT_PREDICATE})"""
    )
    # Append-only: a version documents a template that may already have
    # produced a filed or signed document, so it must never be rewritten or
    # quietly removed.
    #
    # The one permitted delete is the cascade from ``document_templates``,
    # recognised by the parent row already being gone: the referential action
    # fires after the parent delete, so an ordinary application delete (parent
    # still present) is rejected while a genuine cascade proceeds. A history
    # with no subject is not evidence worth keeping.
    op.execute(
        """CREATE OR REPLACE FUNCTION prevent_document_template_version_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND NOT EXISTS (
                   SELECT 1 FROM document_templates template
                   WHERE template.id = OLD.template_id
               )
            THEN RETURN OLD; END IF;
            RAISE EXCEPTION 'document template versions are append-only';
        END;
        $$ LANGUAGE plpgsql"""
    )
    op.execute(
        """CREATE TRIGGER document_template_versions_immutable
        BEFORE UPDATE OR DELETE ON document_template_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_document_template_version_mutation()"""
    )

    op.add_column(
        "document_templates",
        sa.Column("current_version_no", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("document_templates", "current_version_no")
    op.execute(
        "DROP TRIGGER IF EXISTS document_template_versions_immutable "
        "ON document_template_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_document_template_version_mutation()")
    op.execute(
        "DROP POLICY IF EXISTS document_template_versions_tenant_isolation "
        "ON document_template_versions"
    )
    op.drop_index(
        "ix_document_template_versions_tenant_template",
        table_name="document_template_versions",
    )
    op.drop_table("document_template_versions")
