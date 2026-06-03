"""028 — Create cloud_metadata_index table for live-RAG metadata routing.

Revision ID: 028
Revises: 027
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cloud_metadata_index",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("object_type", sa.String(20), nullable=False),
        sa.Column("object_id", sa.String(500), nullable=False),
        sa.Column("parent_id", sa.String(500), nullable=True),
        sa.Column("title", sa.String(1000), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("owner_email", sa.String(300), nullable=True),
        sa.Column("participants", JSONB, nullable=True),
        sa.Column("modified_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mime_type", sa.String(200), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("web_url", sa.Text(), nullable=True),
        sa.Column("sync_cursor", sa.Text(), nullable=True),
        sa.Column(
            "last_synced",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "object_type",
            "object_id",
            name="uq_cloud_metadata_tenant_provider_object",
        ),
    )

    op.create_index(
        "idx_cloud_metadata_tenant",
        "cloud_metadata_index",
        ["tenant_id"],
    )
    op.create_index(
        "idx_cloud_metadata_lookup",
        "cloud_metadata_index",
        ["tenant_id", "provider", "object_type", "modified_time"],
    )

    op.execute("ALTER TABLE cloud_metadata_index ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cloud_metadata_index FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_cloud_metadata ON cloud_metadata_index"
        " USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_cloud_metadata ON cloud_metadata_index"
    )
    op.drop_index("idx_cloud_metadata_lookup", table_name="cloud_metadata_index")
    op.drop_index("idx_cloud_metadata_tenant", table_name="cloud_metadata_index")
    op.drop_table("cloud_metadata_index")
