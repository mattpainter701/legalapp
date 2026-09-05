"""Make template versions exact publication and generation boundaries."""

from alembic import op
import sqlalchemy as sa


revision = "157_template_publication_lifecycle"
down_revision = "156_document_template_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_templates",
        sa.Column("tested_version_no", sa.Integer(), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("published_version_no", sa.Integer(), nullable=True),
    )

    # Version 156 archived the state *before* each edit, leaving the live row
    # one state ahead of current_version_no.  Preserve those rows and append an
    # exact snapshot of the live state so every pointer created from now on has
    # unambiguous content identity.
    op.execute("ALTER TABLE document_template_versions NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        INSERT INTO document_template_versions (
            tenant_id, template_id, version_no, title, body, body_sha256,
            variable_schema, format, category, source_sha256, source_filename,
            is_active, change_summary, created_by_user_id, created_at
        )
        SELECT template.tenant_id, template.id, template.current_version_no + 1,
               template.title, template.body,
               encode(digest(coalesce(template.body, ''), 'sha256'), 'hex'),
               template.variable_schema, template.format, template.category,
               template.source_sha256, template.source_filename,
               template.is_active, 'Lifecycle migration: exact live snapshot',
               NULL, now()
        FROM document_templates AS template
        WHERE template.current_version_no > 0
        """
    )
    op.execute(
        """
        UPDATE document_templates
        SET current_version_no = current_version_no + 1,
            tested_version_no = CASE
                WHEN last_test_rendered_at IS NOT NULL THEN current_version_no + 1
                ELSE NULL
            END,
            published_version_no = CASE
                WHEN is_active AND last_test_rendered_at IS NOT NULL
                    THEN current_version_no + 1
                ELSE NULL
            END,
            status = CASE
                WHEN is_active AND last_test_rendered_at IS NOT NULL
                    THEN 'published'
                WHEN last_test_rendered_at IS NOT NULL
                    THEN 'ready_to_publish'
                ELSE 'draft'
            END,
            is_active = is_active AND last_test_rendered_at IS NOT NULL
        WHERE current_version_no > 0
        """
    )
    op.execute("ALTER TABLE document_template_versions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE document_templates
        SET status = CASE WHEN is_active THEN 'draft' ELSE coalesce(status, 'draft') END,
            is_active = false
        WHERE current_version_no = 0
        """
    )


def downgrade() -> None:
    op.drop_column("document_templates", "published_version_no")
    op.drop_column("document_templates", "tested_version_no")
