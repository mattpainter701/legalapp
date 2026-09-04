"""Add matter document folders and firm-wide document tags.

Matter documents rendered as one flat list with no way to group them. This adds
a per-matter folder tree, a firm-wide tag vocabulary, and the document columns
and link rows that bind them together.

The folder reference on ``matter_documents`` is ON DELETE RESTRICT on purpose:
deleting a folder must never delete or orphan the documents filed in it. The
API re-files or detaches them first, in the same transaction.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "154_matter_document_folders"
down_revision = "153_sms_lifecycle"
branch_labels = None
depends_on = None


TENANT_PREDICATE = (
    "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
)
ROOT_PARENT_SENTINEL = "00000000-0000-0000-0000-000000000000"


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation
        ON {table}
        USING ({TENANT_PREDICATE})
        WITH CHECK ({TENANT_PREDICATE})"""
    )


def upgrade() -> None:
    op.create_table(
        "matter_document_folders",
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
            "parent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matter_document_folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("path", sa.String(1200), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(20), nullable=False, server_default="user"),
        sa.Column("system_key", sa.String(60), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_matter_document_folders_tenant_id"
        ),
        sa.CheckConstraint(
            "depth >= 0 AND depth <= 8", name="ck_matter_document_folders_depth"
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0",
            name="ck_matter_document_folders_name_present",
        ),
        sa.CheckConstraint(
            "position('/' in name) = 0 AND position('\\' in name) = 0",
            name="ck_matter_document_folders_name_no_separator",
        ),
        sa.CheckConstraint(
            "id <> parent_id", name="ck_matter_document_folders_not_self_parent"
        ),
        sa.CheckConstraint(
            "kind IN ('user', 'system')", name="ck_matter_document_folders_kind"
        ),
    )
    # NULL never equals NULL in a unique index, so top-level siblings would
    # otherwise be able to share a name. Fold NULL onto a sentinel uuid.
    op.execute(
        f"""CREATE UNIQUE INDEX uq_matter_document_folders_sibling_name
        ON matter_document_folders (
            tenant_id,
            matter_id,
            coalesce(parent_id, '{ROOT_PARENT_SENTINEL}'::uuid),
            lower(name)
        )"""
    )
    op.create_index(
        "ix_matter_document_folders_tenant_matter",
        "matter_document_folders",
        ["tenant_id", "matter_id"],
    )
    op.create_index(
        "ix_matter_document_folders_tenant_parent",
        "matter_document_folders",
        ["tenant_id", "parent_id"],
    )
    _enable_tenant_rls("matter_document_folders")

    op.create_table(
        "matter_document_tags",
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
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("color", sa.String(20), nullable=False, server_default="slate"),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_matter_document_tags_tenant_id"
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0", name="ck_matter_document_tags_name_present"
        ),
        sa.CheckConstraint(
            "color IN ('slate', 'blue', 'green', 'amber', 'rose', 'purple', 'teal')",
            name="ck_matter_document_tags_color",
        ),
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_matter_document_tags_tenant_name
        ON matter_document_tags (tenant_id, lower(name))"""
    )
    _enable_tenant_rls("matter_document_tags")

    op.add_column(
        "matter_documents", sa.Column("folder_id", UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_matter_documents_tenant_folder",
        "matter_documents",
        "matter_document_folders",
        ["tenant_id", "folder_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_matter_documents_tenant_matter_folder",
        "matter_documents",
        ["tenant_id", "matter_id", "folder_id"],
    )

    op.create_table(
        "matter_document_tag_links",
        # Surrogate key: demo fixture cloning remaps rows by a single UUID id
        # and rejects a clone table keyed on a natural pair.
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["matter_documents.tenant_id", "matter_documents.id"],
            name="fk_matter_document_tag_links_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tag_id"],
            ["matter_document_tags.tenant_id", "matter_document_tags.id"],
            name="fk_matter_document_tag_links_tag",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "tag_id",
            name="uq_matter_document_tag_links_assignment",
        ),
    )
    op.create_index(
        "ix_matter_document_tag_links_tenant_tag",
        "matter_document_tag_links",
        ["tenant_id", "tag_id"],
    )
    _enable_tenant_rls("matter_document_tag_links")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS matter_document_tag_links_tenant_isolation "
        "ON matter_document_tag_links"
    )
    op.drop_index(
        "ix_matter_document_tag_links_tenant_tag",
        table_name="matter_document_tag_links",
    )
    op.drop_table("matter_document_tag_links")

    op.drop_index(
        "idx_matter_documents_tenant_matter_folder", table_name="matter_documents"
    )
    op.drop_constraint(
        "fk_matter_documents_tenant_folder", "matter_documents", type_="foreignkey"
    )
    op.drop_column("matter_documents", "folder_id")

    op.execute(
        "DROP POLICY IF EXISTS matter_document_tags_tenant_isolation "
        "ON matter_document_tags"
    )
    op.execute("DROP INDEX IF EXISTS uq_matter_document_tags_tenant_name")
    op.drop_table("matter_document_tags")

    op.execute(
        "DROP POLICY IF EXISTS matter_document_folders_tenant_isolation "
        "ON matter_document_folders"
    )
    op.drop_index(
        "ix_matter_document_folders_tenant_parent",
        table_name="matter_document_folders",
    )
    op.drop_index(
        "ix_matter_document_folders_tenant_matter",
        table_name="matter_document_folders",
    )
    op.execute("DROP INDEX IF EXISTS uq_matter_document_folders_sibling_name")
    op.drop_table("matter_document_folders")
