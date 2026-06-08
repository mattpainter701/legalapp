"""044 — Client portal (matter-scoped) invites + visibility flags.

Generalizes the mediation portal pattern to firm matters: a tokenized invite
table lets a firm grant a client secure, matter-scoped access to status,
shared documents, messages, and invoices. Adds:
  - client_portal_invites (RLS by tenant)
  - matters.portal_enabled
  - matter_documents.portal_visible (firm controls which case files a client sees)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_portal_invites",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", UUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("created_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_client_portal_invites_token_hash",
        "client_portal_invites",
        ["token_hash"],
    )
    op.create_index(
        "ix_client_portal_invites_matter_id",
        "client_portal_invites",
        ["matter_id"],
    )

    op.execute("ALTER TABLE client_portal_invites ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE client_portal_invites FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_client_portal_invites ON client_portal_invites
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    op.add_column(
        "matters",
        sa.Column(
            "portal_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "matter_documents",
        sa.Column(
            "portal_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("matter_documents", "portal_visible")
    op.drop_column("matters", "portal_enabled")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_client_portal_invites "
        "ON client_portal_invites"
    )
    op.execute(
        "ALTER TABLE client_portal_invites DISABLE ROW LEVEL SECURITY"
    )
    op.drop_index(
        "ix_client_portal_invites_matter_id", table_name="client_portal_invites"
    )
    op.drop_index(
        "ix_client_portal_invites_token_hash", table_name="client_portal_invites"
    )
    op.drop_table("client_portal_invites")
