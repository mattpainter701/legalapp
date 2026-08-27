"""Saved conflict checks and portal invoice-download audit metadata.

Revision ID: 135_conflict_invoice_audit
Revises: 134_background_ai_quota
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "135_conflict_invoice_audit"
down_revision = "134_background_ai_quota"
branch_labels = None
depends_on = None


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
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


def upgrade() -> None:
    op.create_table(
        "conflict_checks",
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
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column(
            "query_snapshot",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "result_snapshot",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "restricted_matter_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column(
            "decision",
            sa.String(40),
            nullable=False,
            server_default="needs_review",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "closed_by_user_id",
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
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'closed')", name="ck_conflict_checks_status"
        ),
        sa.CheckConstraint(
            "decision IN ('needs_review', 'no_conflict_found', 'conflict_found', 'cleared_with_conditions')",
            name="ck_conflict_checks_decision",
        ),
        sa.CheckConstraint(
            "match_count >= 0", name="ck_conflict_checks_match_count"
        ),
        sa.CheckConstraint(
            "restricted_matter_count >= 0",
            name="ck_conflict_checks_restricted_count",
        ),
    )
    op.create_index(
        "idx_conflict_checks_tenant_created",
        "conflict_checks",
        ["tenant_id", "created_at"],
    )
    op.create_index("idx_conflict_checks_matter", "conflict_checks", ["matter_id"])
    op.create_index(
        "idx_conflict_checks_creator",
        "conflict_checks",
        ["tenant_id", "created_by_user_id"],
    )

    op.create_table(
        "portal_invoice_downloads",
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
            "invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invite_id",
            UUID(as_uuid=True),
            sa.ForeignKey("client_portal_invites.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recipient_email", sa.String(320), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column(
            "branding_snapshot",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "content_length > 0", name="ck_portal_invoice_download_content_length"
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_portal_invoice_download_sha256",
        ),
    )
    op.create_index(
        "idx_portal_invoice_downloads_tenant_invoice",
        "portal_invoice_downloads",
        ["tenant_id", "invoice_id", "downloaded_at"],
    )
    op.create_index(
        "idx_portal_invoice_downloads_invite",
        "portal_invoice_downloads",
        ["invite_id"],
    )

    _tenant_rls("conflict_checks")
    _tenant_rls("portal_invoice_downloads")


def downgrade() -> None:
    op.drop_table("portal_invoice_downloads")
    op.drop_table("conflict_checks")
