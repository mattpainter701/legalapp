"""Add one-time file-open intents and agent-owned source identities."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "149_file_open_intents"
down_revision = "148_configurable_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "smb_file_index", sa.Column("source_id", UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "smb_file_index", sa.Column("file_revision", sa.String(200), nullable=True)
    )
    op.create_unique_constraint(
        "uq_smb_file_tenant_agent_source",
        "smb_file_index",
        ["tenant_id", "agent_id", "source_id"],
    )
    op.create_table(
        "file_open_intents",
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
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            UUID(as_uuid=True),
            sa.ForeignKey("smb_file_index.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("smb_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "share_id",
            UUID(as_uuid=True),
            sa.ForeignKey("smb_shares.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
        ),
        sa.Column("revision", sa.String(200), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("handle_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True)),
        sa.Column("redeemed_session_id", sa.String(32)),
        sa.Column("redeemed_user_sid_hash", sa.String(64)),
        sa.Column("outcome", sa.String(40)),
        sa.Column("last_failure", sa.String(40)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("handle_hash", name="uq_file_open_intents_handle_hash"),
        sa.UniqueConstraint("nonce", name="uq_file_open_intents_nonce"),
    )
    op.create_index(
        "ix_file_open_intents_tenant_expires",
        "file_open_intents",
        ["tenant_id", "expires_at"],
    )
    op.execute("ALTER TABLE file_open_intents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE file_open_intents FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY file_open_intents_tenant_isolation
        ON file_open_intents
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)"""
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS file_open_intents_tenant_isolation ON file_open_intents"
    )
    op.drop_index("ix_file_open_intents_tenant_expires", table_name="file_open_intents")
    op.drop_table("file_open_intents")
    op.drop_constraint(
        "uq_smb_file_tenant_agent_source", "smb_file_index", type_="unique"
    )
    op.drop_column("smb_file_index", "file_revision")
    op.drop_column("smb_file_index", "source_id")
