"""031 — Mediation Platform module

Revision ID: 031
Revises: 030_user_sync
Create Date: 2026-06-04

Builds out the Mediation Platform: expands ``mediation_cases`` /
``mediation_case_events`` with the domestic-mediation fields the portal/detail
UI expects, and adds the party, invite, asset-schedule, document-vault and
proposal tables. Every new table is tenant-isolated with the same RLS pattern
used by the Trust & Estate tables (see migration 008).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "031"
down_revision: Union[str, None] = "030_user_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_rls_direct(table: str) -> None:
    """RLS for tables that carry their own tenant_id column."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def upgrade() -> None:
    # ── 1. Expand mediation_cases ──────────────────────────────────────────────
    op.add_column("mediation_cases", sa.Column("case_name", sa.String(500), nullable=True))
    op.add_column("mediation_cases", sa.Column("party_a", sa.String(300), nullable=True))
    op.add_column("mediation_cases", sa.Column("party_b", sa.String(300), nullable=True))
    op.add_column("mediation_cases", sa.Column("dispute_type", sa.String(150), nullable=True))
    op.add_column("mediation_cases", sa.Column("mediation_stage", sa.String(100), nullable=True))
    op.add_column("mediation_cases", sa.Column("mediator", sa.String(300), nullable=True))
    op.add_column("mediation_cases", sa.Column("attorney", sa.String(300), nullable=True))
    op.add_column("mediation_cases", sa.Column("claim_value", sa.String(100), nullable=True))
    op.add_column(
        "mediation_cases",
        sa.Column("scheduled_session", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mediation_cases",
        sa.Column(
            "confidentiality_signed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "mediation_cases",
        sa.Column(
            "matter_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "mediation_cases",
        sa.Column(
            "client_contact_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── 2. Expand mediation_case_events ────────────────────────────────────────
    op.add_column(
        "mediation_case_events", sa.Column("session_type", sa.String(100), nullable=True)
    )
    op.add_column(
        "mediation_case_events", sa.Column("added_by", sa.String(300), nullable=True)
    )

    # ── 3. mediation_parties ───────────────────────────────────────────────────
    op.create_table(
        "mediation_parties",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(50), nullable=False, server_default="our_client"),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column(
            "contact_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_initiator", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mediation_parties_tenant_id", "mediation_parties", ["tenant_id"])
    op.create_index("ix_mediation_parties_case_id", "mediation_parties", ["case_id"])

    # ── 4. mediation_invites ───────────────────────────────────────────────────
    op.create_table(
        "mediation_invites",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "party_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_parties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False, server_default="portal_magic"),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mediation_invites_tenant_id", "mediation_invites", ["tenant_id"])
    op.create_index("ix_mediation_invites_case_id", "mediation_invites", ["case_id"])
    op.create_index("ix_mediation_invites_token_hash", "mediation_invites", ["token_hash"])

    # ── 5. mediation_assets ────────────────────────────────────────────────────
    op.create_table(
        "mediation_assets",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False, server_default="asset"),
        sa.Column("category", sa.String(150), nullable=True),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("value", sa.Numeric(14, 2), nullable=True),
        sa.Column("owned_by", sa.String(20), nullable=True),
        sa.Column("claimed_by", sa.String(150), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="draft"),
        sa.Column(
            "submitted_by_party_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_parties.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attorney_approved_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attorney_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opposing_decision", sa.String(30), nullable=True),
        sa.Column("opposing_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispute_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mediation_assets_tenant_id", "mediation_assets", ["tenant_id"])
    op.create_index("ix_mediation_assets_case_id", "mediation_assets", ["case_id"])

    # ── 6. mediation_documents ─────────────────────────────────────────────────
    op.create_table(
        "mediation_documents",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_by_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_by_party_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_parties.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("storage_path", sa.String(1000), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mediation_documents_tenant_id", "mediation_documents", ["tenant_id"])
    op.create_index("ix_mediation_documents_case_id", "mediation_documents", ["case_id"])

    # ── 7. mediation_proposals ─────────────────────────────────────────────────
    op.create_table(
        "mediation_proposals",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "case_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposed_by_party_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_parties.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "parent_proposal_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_proposals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_mediation_proposals_tenant_id", "mediation_proposals", ["tenant_id"])
    op.create_index("ix_mediation_proposals_case_id", "mediation_proposals", ["case_id"])

    # ── 8. Row-Level Security ──────────────────────────────────────────────────
    for table in (
        "mediation_parties",
        "mediation_invites",
        "mediation_assets",
        "mediation_documents",
        "mediation_proposals",
    ):
        _enable_rls_direct(table)


def downgrade() -> None:
    for table in (
        "mediation_proposals",
        "mediation_documents",
        "mediation_assets",
        "mediation_invites",
        "mediation_parties",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)

    op.drop_column("mediation_case_events", "added_by")
    op.drop_column("mediation_case_events", "session_type")

    for col in (
        "client_contact_id",
        "matter_id",
        "confidentiality_signed",
        "scheduled_session",
        "claim_value",
        "attorney",
        "mediator",
        "mediation_stage",
        "dispute_type",
        "party_b",
        "party_a",
        "case_name",
    ):
        op.drop_column("mediation_cases", col)
