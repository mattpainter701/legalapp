"""050 — Native e-signature (Epic 2).

Adds a swappable e-signature request model so a firm can send a matter document
out for signature and have signers execute it in the client portal. Two tables:

  - signature_requests (RLS by tenant): one per send, tracks status/provider.
  - signature_signers   (RLS by tenant): one row per signer with the captured
    typed signature, timestamp, IP, and an audit JSON blob.

The ``internal`` provider lets signers sign in the client portal; on completion
an audit-certificate PDF is generated and stored as a portal-visible
``MatterDocument`` (the executed copy). Real providers (Dropbox Sign / DocuSign)
are stubbed for later wiring via the same interface.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signature_requests",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("matter_id", UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column(
            "provider",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'internal'"),
        ),
        sa.Column("provider_envelope_id", sa.String(200), nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matter_id"], ["matters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["matter_documents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_signature_requests_matter_id",
        "signature_requests",
        ["matter_id"],
    )

    op.execute("ALTER TABLE signature_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE signature_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_signature_requests ON signature_requests
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    op.create_table(
        "signature_signers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "sign_order", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_ip", sa.String(64), nullable=True),
        sa.Column("typed_signature", sa.Text(), nullable=True),
        sa.Column("audit", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["request_id"], ["signature_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_signature_signers_request_id",
        "signature_signers",
        ["request_id"],
    )

    op.execute("ALTER TABLE signature_signers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE signature_signers FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_signature_signers ON signature_signers
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_signature_signers "
        "ON signature_signers"
    )
    op.execute("ALTER TABLE signature_signers DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_signature_signers_request_id", table_name="signature_signers")
    op.drop_table("signature_signers")

    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_signature_requests "
        "ON signature_requests"
    )
    op.execute("ALTER TABLE signature_requests DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_signature_requests_matter_id", table_name="signature_requests")
    op.drop_table("signature_requests")
