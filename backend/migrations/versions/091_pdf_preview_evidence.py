"""Persist value-bound PDF preview evidence.

Revision ID: 091_pdf_preview_evidence
Revises: 090_zoom_account_binding
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "091_pdf_preview_evidence"
down_revision = "090_zoom_account_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_template_previews",
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
            sa.ForeignKey("document_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "previewed_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("contract_sha256", sa.String(64), nullable=False),
        sa.Column("values_hmac_sha256", sa.String(64), nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=False),
        sa.Column("renderer_version", sa.String(50), nullable=False),
        sa.Column(
            "flatten_pdf", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("reviewed_field_count", sa.Integer(), nullable=False),
        sa.Column("nonblank_field_count", sa.Integer(), nullable=False),
        sa.Column("reviewed_field_names", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consumed_by_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matter_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "reconciliation_required_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("reconciliation_reason", sa.String(40), nullable=True),
        sa.Column("reconciliation_storage_backend", sa.String(50), nullable=True),
        sa.Column("reconciliation_provider_item_id", sa.String(500), nullable=True),
        sa.Column("reconciliation_provider_drive_id", sa.String(500), nullable=True),
        sa.Column("reconciliation_local_path", sa.String(1000), nullable=True),
        sa.Column("reconciliation_output_filename", sa.String(500), nullable=True),
        sa.Column("reconciliation_output_sha256", sa.String(64), nullable=True),
        sa.Column("reconciliation_document_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "reconciliation_resolved_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("reconciliation_resolution", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "purpose IN ('draft', 'activation', 'generation')",
            name="ck_document_template_previews_purpose",
        ),
        sa.CheckConstraint(
            "reviewed_field_count >= 0 AND nonblank_field_count >= 0 "
            "AND nonblank_field_count <= reviewed_field_count",
            name="ck_document_template_previews_field_counts",
        ),
        sa.CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND reconciliation_required_at IS NOT NULL "
            "AND reconciliation_resolved_at IS NULL)",
            name="ck_document_template_previews_terminal_state",
        ),
        sa.CheckConstraint(
            "(reconciliation_required_at IS NULL AND reconciliation_reason IS NULL) "
            "OR (reconciliation_required_at IS NOT NULL AND reconciliation_reason "
            "IN ('cleanup_failed', 'commit_outcome_unknown'))",
            name="ck_document_template_previews_reconciliation_reason",
        ),
        sa.CheckConstraint(
            "(reconciliation_resolved_at IS NULL AND reconciliation_resolution IS NULL) "
            "OR (reconciliation_resolved_at IS NOT NULL AND "
            "reconciliation_required_at IS NOT NULL AND "
            "reconciliation_resolution IS NOT NULL)",
            name="ck_document_template_previews_reconciliation_resolution",
        ),
    )
    op.create_index(
        "idx_document_template_previews_lookup",
        "document_template_previews",
        [
            "tenant_id",
            "template_id",
            "previewed_by_user_id",
            "purpose",
            "created_at",
        ],
    )
    op.execute("ALTER TABLE document_template_previews ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY document_template_previews_tenant_isolation
        ON document_template_previews
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )
    op.execute("ALTER TABLE document_template_previews FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index(
        "idx_document_template_previews_lookup",
        table_name="document_template_previews",
    )
    op.drop_table("document_template_previews")
