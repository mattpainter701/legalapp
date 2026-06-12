"""051 — Chat attachment storage.

Adds chat-attachment linkage to the documents table (Tier 1 — session
attachments, see docs/ARCHITECTURE.md):

  - conversation_id: links a document to the chat conversation it was
    attached to (CASCADE delete — removing a conversation removes its
    attachments).
  - matter_id: for matter-linked conversations, denormalized from
    conversations.matter_id so chat attachments can be queried/stored
    alongside the matter's chatattachments subdirectory.
  - expires_at: rolling-deletion deadline for misc-chat (non-matter)
    attachments stored in temp storage; NULL means the attachment persists
    (matter-linked chat attachments, and any pre-existing documents).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("matter_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_documents_conversation_id",
        "documents",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_documents_matter_id",
        "documents",
        "matters",
        ["matter_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_documents_conversation_id", "documents", ["conversation_id"])
    op.create_index("ix_documents_expires_at", "documents", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_documents_expires_at", table_name="documents")
    op.drop_index("ix_documents_conversation_id", table_name="documents")
    op.drop_constraint("fk_documents_matter_id", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_conversation_id", "documents", type_="foreignkey")
    op.drop_column("documents", "expires_at")
    op.drop_column("documents", "matter_id")
    op.drop_column("documents", "conversation_id")
