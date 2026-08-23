"""Per-tenant SMB credential vault and richer share configuration.

Before this revision the only way an agent could authenticate to a file server
was a username/password pair typed into its local ``config.toml`` at pairing
time, shared by every share that agent scanned. This adds:

* ``smb_credentials`` — tenant-scoped, RLS-protected credential records whose
  secret is stored as a Fernet ciphertext from the ``TOKEN_ENCRYPTION_KEYS``
  keyring (never plaintext, never returned to admin callers);
* ``smb_shares.credential_id`` — the credential a share mounts with, so one
  agent can serve shares that need different identities;
* scan/verify result columns so the admin console can show why a share is not
  indexing instead of an empty "last scan" cell;
* a wider ``smb_agents.pairing_code``: the generated code is 22 characters and
  the column was ``varchar(20)``, so issuing a pairing code failed outright.

Revision ID: 121_smb_share_credentials
Revises: 120_marketing_demo_funnel
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision = "121_smb_share_credentials"
down_revision = "120_marketing_demo_funnel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "smb_credentials",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("auth_method", sa.String(20), nullable=False, server_default="ntlm"),
        sa.Column("domain", sa.String(200), nullable=True),
        sa.Column("username", sa.String(200), nullable=True),
        # Fernet ciphertext; null for kerberos / guest auth.
        sa.Column("encrypted_password", sa.Text(), nullable=True),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verify_status", sa.String(20), nullable=True),
        sa.Column("last_verify_error", sa.Text(), nullable=True),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["smb_agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_smb_credentials_tenant_name"),
    )
    op.create_index(
        "ix_smb_credentials_tenant_id", "smb_credentials", ["tenant_id"]
    )

    op.execute("ALTER TABLE smb_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE smb_credentials FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_smb_credentials
        ON smb_credentials
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    # secrets.token_urlsafe(16) is 22 characters; the original varchar(20)
    # rejected every pairing code the API generated.
    op.alter_column(
        "smb_agents",
        "pairing_code",
        existing_type=sa.String(20),
        type_=sa.String(64),
        existing_nullable=True,
    )

    op.add_column(
        "smb_shares", sa.Column("credential_id", UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_smb_shares_credential_id",
        "smb_shares",
        "smb_credentials",
        ["credential_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("smb_shares", sa.Column("last_scan_error", sa.Text(), nullable=True))
    op.add_column(
        "smb_shares",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "smb_shares", sa.Column("last_verify_status", sa.String(20), nullable=True)
    )
    op.add_column(
        "smb_shares", sa.Column("last_verify_error", sa.Text(), nullable=True)
    )
    op.add_column(
        "smb_shares", sa.Column("exclude_patterns", ARRAY(sa.String()), nullable=True)
    )
    op.add_column(
        "smb_shares",
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default="true"
        ),
    )


def downgrade() -> None:
    op.alter_column(
        "smb_agents",
        "pairing_code",
        existing_type=sa.String(64),
        type_=sa.String(20),
        existing_nullable=True,
    )
    op.drop_column("smb_shares", "is_enabled")
    op.drop_column("smb_shares", "exclude_patterns")
    op.drop_column("smb_shares", "last_verify_error")
    op.drop_column("smb_shares", "last_verify_status")
    op.drop_column("smb_shares", "last_verified_at")
    op.drop_column("smb_shares", "last_scan_error")
    op.drop_constraint("fk_smb_shares_credential_id", "smb_shares", type_="foreignkey")
    op.drop_column("smb_shares", "credential_id")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_smb_credentials ON smb_credentials")
    op.execute("ALTER TABLE smb_credentials DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_smb_credentials_tenant_id", table_name="smb_credentials")
    op.drop_table("smb_credentials")
