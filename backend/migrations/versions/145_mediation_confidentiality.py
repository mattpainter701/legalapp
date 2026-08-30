"""Add recipient-scoped mediation releases and proposal review evidence.

Revision ID: 145_mediation_confidentiality
Revises: 144_brief_checks
Create Date: 2026-08-30

Existing mediation documents and proposals intentionally remain unreleased.
That fail-closed backfill prevents a deployment from preserving the legacy
behavior where every portal party could see every case artifact.
"""

from alembic import op
import sqlalchemy as sa


revision = "145_mediation_confidentiality"
down_revision = "144_brief_checks"
branch_labels = None
depends_on = None


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (
            tenant_id = NULLIF(
                current_setting('app.current_tenant_id', true), ''
            )::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.current_tenant_id', true), ''
            )::uuid
        )
        """
    )


def upgrade() -> None:
    op.add_column(
        "mediation_documents",
        sa.Column("content_sha256", sa.String(64), nullable=True),
    )

    op.add_column(
        "mediation_proposals",
        sa.Column(
            "review_state",
            sa.String(30),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "mediation_proposals", sa.Column("review_notes", sa.Text(), nullable=True)
    )
    op.add_column(
        "mediation_proposals",
        sa.Column(
            "reviewed_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "mediation_proposals",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mediation_proposals",
        sa.Column(
            "released_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "mediation_proposals",
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mediation_proposals",
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "mediation_proposals",
        sa.Column("content_sha256", sa.String(64), nullable=True),
    )

    op.create_table(
        "mediation_document_recipients",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "document_id",
            sa.UUID(),
            sa.ForeignKey("mediation_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "party_id",
            sa.UUID(),
            sa.ForeignKey("mediation_parties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "released_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("first_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "document_id", "party_id", name="uq_mediation_document_recipient"
        ),
    )
    op.create_index(
        "ix_mediation_document_recipients_tenant",
        "mediation_document_recipients",
        ["tenant_id"],
    )
    op.create_index(
        "ix_mediation_document_recipients_document",
        "mediation_document_recipients",
        ["document_id"],
    )
    op.create_index(
        "ix_mediation_document_recipients_party",
        "mediation_document_recipients",
        ["party_id"],
    )

    op.create_table(
        "mediation_proposal_recipients",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "proposal_id",
            sa.UUID(),
            sa.ForeignKey("mediation_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "party_id",
            sa.UUID(),
            sa.ForeignKey("mediation_parties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "released_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("first_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "proposal_id", "party_id", name="uq_mediation_proposal_recipient"
        ),
    )
    op.create_index(
        "ix_mediation_proposal_recipients_tenant",
        "mediation_proposal_recipients",
        ["tenant_id"],
    )
    op.create_index(
        "ix_mediation_proposal_recipients_proposal",
        "mediation_proposal_recipients",
        ["proposal_id"],
    )
    op.create_index(
        "ix_mediation_proposal_recipients_party",
        "mediation_proposal_recipients",
        ["party_id"],
    )

    _enable_tenant_rls("mediation_document_recipients")
    _enable_tenant_rls("mediation_proposal_recipients")


def downgrade() -> None:
    for table in (
        "mediation_proposal_recipients",
        "mediation_document_recipients",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index(
        "ix_mediation_proposal_recipients_party",
        table_name="mediation_proposal_recipients",
    )
    op.drop_index(
        "ix_mediation_proposal_recipients_proposal",
        table_name="mediation_proposal_recipients",
    )
    op.drop_index(
        "ix_mediation_proposal_recipients_tenant",
        table_name="mediation_proposal_recipients",
    )
    op.drop_table("mediation_proposal_recipients")

    op.drop_index(
        "ix_mediation_document_recipients_party",
        table_name="mediation_document_recipients",
    )
    op.drop_index(
        "ix_mediation_document_recipients_document",
        table_name="mediation_document_recipients",
    )
    op.drop_index(
        "ix_mediation_document_recipients_tenant",
        table_name="mediation_document_recipients",
    )
    op.drop_table("mediation_document_recipients")

    for column in (
        "content_sha256",
        "created_by_user_id",
        "released_at",
        "released_by_user_id",
        "reviewed_at",
        "reviewed_by_user_id",
        "review_notes",
        "review_state",
    ):
        op.drop_column("mediation_proposals", column)
    op.drop_column("mediation_documents", "content_sha256")
