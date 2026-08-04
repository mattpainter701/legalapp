"""Add bounded matter-document revision proposals.

Revision ID: 101_doc_revisions
Revises: 100_task_work_board
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "101_doc_revisions"
down_revision = "100_task_work_board"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "matter_document_revisions",
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
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "root_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matter_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matter_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_revision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matter_document_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "output_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matter_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "requested_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "approved_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "rejected_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("client_request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="processing",
        ),
        sa.Column("clarification_question", sa.Text(), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.String(length=1000), nullable=True),
        sa.Column(
            "warnings", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.Column(
            "operations",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "output_text_preview",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("requested_model_tier", sa.String(length=20), nullable=False),
        sa.Column("resolved_model_tier", sa.String(length=30), nullable=True),
        sa.Column("model_alias", sa.String(length=200), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("storage_warning", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column(
            "prepared_esign_signature_request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("signature_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "prepared_esign_snapshot_hmac_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("prepared_esign_preview", sa.JSON(), nullable=True),
        sa.Column("prepared_esign_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "prepared_esign_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=1000), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
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
            name="uq_doc_revisions_tenant_client_request",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "root_document_id",
            "version_no",
            name="uq_doc_revisions_root_version",
        ),
        sa.UniqueConstraint(
            "output_document_id", name="uq_doc_revisions_output_document"
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'needs_input', 'ready_for_review', "
            "'approved', 'rejected', 'superseded', 'failed')",
            name="ck_doc_revisions_status",
        ),
        sa.CheckConstraint("version_no > 0", name="ck_doc_revisions_version_positive"),
        sa.CheckConstraint(
            "status NOT IN ('ready_for_review', 'approved', 'rejected', "
            "'superseded') OR "
            "(output_document_id IS NOT NULL AND output_sha256 IS NOT NULL)",
            name="ck_doc_revisions_output_required",
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR approved_at IS NOT NULL",
            name="ck_doc_revisions_approval_evidence",
        ),
    )
    op.create_index(
        "ix_doc_revisions_tenant_matter_created",
        "matter_document_revisions",
        ["tenant_id", "matter_id", "created_at"],
    )
    op.create_index(
        "ix_doc_revisions_tenant_root_version",
        "matter_document_revisions",
        ["tenant_id", "root_document_id", "version_no"],
    )
    op.execute("ALTER TABLE matter_document_revisions ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY matter_document_revisions_tenant_isolation
        ON matter_document_revisions
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
    op.execute("ALTER TABLE matter_document_revisions FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index(
        "ix_doc_revisions_tenant_root_version",
        table_name="matter_document_revisions",
    )
    op.drop_index(
        "ix_doc_revisions_tenant_matter_created",
        table_name="matter_document_revisions",
    )
    op.drop_table("matter_document_revisions")
