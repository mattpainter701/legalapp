"""036 — SMB file share relay agent tables.

Revision ID: 036
Revises: 035
Create Date: 2026-06-04

Adds five tables for on-prem SMB file share scanning and metadata-only indexing:
- smb_agents: relay agents that scan on-prem file shares
- smb_shares: file share paths configured per agent
- smb_file_index: metadata-only index entries (never content, only snippets)
- smb_access_log: audit trail for content fetched through agent relay
- matter_smb_shares: join table linking matters to SMB shares

Also adds smb_folders JSONB column to matters table.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None

RLS_TABLES = ["smb_agents", "smb_shares", "smb_file_index", "smb_access_log", "matter_smb_shares"]


def upgrade() -> None:
    # ── smb_agents ──────────────────────────────────────────────────────────
    op.create_table(
        "smb_agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(200), nullable=False),
        sa.Column("api_key_hash", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("agent_version", sa.String(50), nullable=True),
        sa.Column("hostname", sa.String(200), nullable=True),
        sa.Column("os_info", sa.String(200), nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pairing_code", sa.String(20), nullable=True, unique=True),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    # ── smb_shares ──────────────────────────────────────────────────────────
    op.create_table(
        "smb_shares",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("share_path", sa.String(500), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("file_extensions", ARRAY(sa.String()), nullable=True),
        sa.Column("max_depth", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("scan_schedule", sa.String(50), nullable=False, server_default="0 */6 * * *"),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_status", sa.String(20), nullable=True),
        sa.Column("last_scan_file_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["agent_id"], ["smb_agents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("agent_id", "share_path", name="uq_smb_shares_agent_path"),
    )

    # ── smb_file_index ──────────────────────────────────────────────────────
    op.create_table(
        "smb_file_index",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("share_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("ext", sa.String(20), nullable=True),
        sa.Column("mime_type", sa.String(200), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(300), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("modified_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("search_vector", TSVECTOR, nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["share_id"], ["smb_shares.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_id"], ["smb_agents.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "path", name="uq_smb_file_tenant_path"),
    )
    op.create_index("ix_smb_file_index_tenant_share", "smb_file_index", ["tenant_id", "share_id"])
    op.create_index("ix_smb_file_index_tenant_ext", "smb_file_index", ["tenant_id", "ext"])
    op.create_index(
        "ix_smb_file_index_search_vector",
        "smb_file_index",
        ["search_vector"],
        postgresql_using="gin",
    )

    # ── Auto-update search_vector trigger ────────────────────────────────────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION smb_file_index_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector(
                'english',
                COALESCE(NEW.snippet, '') || ' ' || COALESCE(NEW.filename, '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_smb_file_index_search_vector
        BEFORE INSERT OR UPDATE OF snippet, filename ON smb_file_index
        FOR EACH ROW EXECUTE FUNCTION smb_file_index_search_vector_update()
        """
    )

    # ── smb_access_log ───────────────────────────────────────────────────────
    op.create_table(
        "smb_access_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("access_reason", sa.String(50), nullable=True),
        sa.Column("bytes_sent", sa.Integer(), nullable=True),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_id"], ["smb_agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
    )

    # ── matter_smb_shares ──────────────────────────────────────────────────
    op.create_table(
        "matter_smb_shares",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", UUID(as_uuid=True), nullable=False),
        sa.Column("share_id", UUID(as_uuid=True), nullable=False),
        sa.Column("folder_path", sa.String(500), nullable=True),
        sa.Column("display_label", sa.String(200), nullable=True),
        sa.Column("auto_scan", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["share_id"], ["smb_shares.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("matter_id", "share_id", "folder_path", name="uq_matter_smb_share"),
    )

    # ── RLS policies ────────────────────────────────────────────────────────
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        policy_name = f"tenant_isolation_{table}"
        op.execute(
            f"""
            CREATE POLICY {policy_name}
            ON {table}
            FOR ALL TO PUBLIC
            USING (
                tenant_id::text = current_setting('app.current_tenant_id', true)
            )
            """
        )

    # ── Add smb_folders JSONB column to matters ─────────────────────────────
    op.add_column("matters", sa.Column("smb_folders", JSONB(), nullable=True))


def downgrade() -> None:
    # Remove smb_folders from matters
    op.drop_column("matters", "smb_folders")

    # Drop RLS policies and disable RLS
    for table in RLS_TABLES:
        policy_name = f"tenant_isolation_{table}"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS trg_smb_file_index_search_vector ON smb_file_index")
    op.execute("DROP FUNCTION IF EXISTS smb_file_index_search_vector_update()")

    # Drop indexes
    op.drop_index("ix_smb_file_index_search_vector", table_name="smb_file_index")
    op.drop_index("ix_smb_file_index_tenant_ext", table_name="smb_file_index")
    op.drop_index("ix_smb_file_index_tenant_share", table_name="smb_file_index")

    # Drop tables in reverse dependency order
    op.drop_table("matter_smb_shares")
    op.drop_table("smb_access_log")
    op.drop_table("smb_file_index")
    op.drop_table("smb_shares")
    op.drop_table("smb_agents")