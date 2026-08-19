"""Add task linkage to matter documents and chat artifact pipeline.

Revision ID: 110_chat_artifact_pipeline
Revises: 109_hide_unlinked_synced_emails
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "110_chat_artifact_pipeline"
down_revision = "109_hide_unlinked_synced_emails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matter_documents",
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_matter_documents_task_id",
        "matter_documents",
        ["task_id"],
    )

    op.create_table(
        "chat_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("format", sa.String(50), nullable=False, server_default="markdown"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("matter_id", UUID(as_uuid=True), sa.ForeignKey("matters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("saved_to_matter", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("saved_document_id", UUID(as_uuid=True), sa.ForeignKey("matter_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "idx_chat_artifacts_tenant_conversation",
        "chat_artifacts",
        ["tenant_id", "conversation_id"],
    )
    op.create_index(
        "idx_chat_artifacts_matter_id",
        "chat_artifacts",
        ["matter_id"],
    )
    op.create_index(
        "idx_chat_artifacts_task_id",
        "chat_artifacts",
        ["task_id"],
    )
    op.execute("ALTER TABLE chat_artifacts ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY chat_artifacts_tenant_isolation ON chat_artifacts
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE chat_artifacts FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("idx_chat_artifacts_task_id", table_name="chat_artifacts")
    op.drop_index("idx_chat_artifacts_matter_id", table_name="chat_artifacts")
    op.drop_index("idx_chat_artifacts_tenant_conversation", table_name="chat_artifacts")
    op.drop_table("chat_artifacts")
    op.drop_index("idx_matter_documents_task_id", table_name="matter_documents")
    op.drop_column("matter_documents", "task_id")
