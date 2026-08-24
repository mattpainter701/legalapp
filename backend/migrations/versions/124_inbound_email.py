"""Add secure matter email aliases and an inbound review queue.

Revision ID: 124_inbound_email
Revises: 123_client_portal_activity
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "124_inbound_email"
down_revision = "123_client_portal_activity"
branch_labels = None
depends_on = None


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        """
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        "inbound_email_aliases",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False, server_default="matter"),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("encrypted_local_part", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("kind = 'matter'", name="ck_inbound_alias_kind"),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_inbound_alias_status"
        ),
    )
    op.create_index("idx_inbound_aliases_tenant", "inbound_email_aliases", ["tenant_id"])
    op.create_index(
        "idx_inbound_aliases_matter",
        "inbound_email_aliases",
        ["tenant_id", "matter_id"],
    )
    op.create_index(
        "uq_inbound_alias_active_matter",
        "inbound_email_aliases",
        ["tenant_id", "matter_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "inbound_emails",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "alias_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inbound_email_aliases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("envelope_sender", sa.String(320), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body_preview", sa.Text(), nullable=True),
        sa.Column("participants", JSONB(), nullable=True),
        sa.Column("authentication_results", JSONB(), nullable=True),
        sa.Column("provider_message_id", sa.String(500), nullable=True),
        sa.Column("message_sha256", sa.String(64), nullable=False),
        sa.Column("raw_storage_path", sa.Text(), nullable=True),
        sa.Column("raw_size", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reviewed_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "communication_log_id",
            UUID(as_uuid=True),
            sa.ForeignKey("communication_logs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "alias_id", "message_sha256", name="uq_inbound_email_alias_sha256"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_inbound_email_status",
        ),
    )
    op.create_index(
        "idx_inbound_emails_tenant_status",
        "inbound_emails",
        ["tenant_id", "status"],
    )
    op.create_index(
        "idx_inbound_emails_matter_status",
        "inbound_emails",
        ["matter_id", "status"],
    )
    op.create_index(
        "idx_inbound_emails_created",
        "inbound_emails",
        ["tenant_id", "created_at"],
    )

    _tenant_rls("inbound_email_aliases")
    _tenant_rls("inbound_emails")

    # The signed ingress endpoint briefly enables only this SELECT policy to
    # turn an opaque local part into a tenant id. It cannot insert, update, or
    # read queued mail until the normal tenant context is established.
    op.execute(
        """
        CREATE POLICY inbound_email_aliases_route_lookup
        ON inbound_email_aliases FOR SELECT
        USING (current_setting('app.inbound_email_route_lookup', true) = 'on')
        """
    )


def downgrade() -> None:
    op.drop_table("inbound_emails")
    op.drop_table("inbound_email_aliases")
