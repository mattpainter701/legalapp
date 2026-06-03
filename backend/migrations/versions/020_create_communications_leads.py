"""020 — Create communication_logs and leads tables.

Revision ID: 020
Revises: 019
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- communication_logs ---
    op.create_table(
        "communication_logs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "direction", sa.String(20), nullable=False, server_default="outbound"
        ),
        sa.Column(
            "channel", sa.String(30), nullable=False, server_default="email"
        ),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="logged"
        ),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("external_ref", sa.String(500), nullable=True),
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
    )

    op.create_index("idx_commlogs_tenant_id", "communication_logs", ["tenant_id"])
    op.create_index("idx_commlogs_matter_id", "communication_logs", ["matter_id"])
    op.create_index("idx_commlogs_contact_id", "communication_logs", ["contact_id"])
    op.create_index(
        "idx_commlogs_occurred_at",
        "communication_logs",
        ["tenant_id", "occurred_at"],
    )

    op.execute("ALTER TABLE communication_logs ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY commlogs_tenant_isolation ON communication_logs
        USING (tenant_id = current_setting('app.tenant_id')::uuid)
    """)

    # --- leads ---
    op.create_table(
        "leads",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="new"),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("practice_area", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("estimated_value", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "assigned_to_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "conflict_check_status",
            sa.String(50),
            nullable=False,
            server_default="not_run",
        ),
        sa.Column("conflict_check_notes", sa.Text(), nullable=True),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("declined_reason", sa.String(500), nullable=True),
        sa.Column(
            "created_by_user_id",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("idx_leads_tenant_id", "leads", ["tenant_id"])
    op.create_index("idx_leads_contact_id", "leads", ["contact_id"])
    op.create_index("idx_leads_status", "leads", ["tenant_id", "status"])

    op.execute("ALTER TABLE leads ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY leads_tenant_isolation ON leads
        USING (tenant_id = current_setting('app.tenant_id')::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS leads_tenant_isolation ON leads")
    op.drop_table("leads")
    op.execute(
        "DROP POLICY IF EXISTS commlogs_tenant_isolation ON communication_logs"
    )
    op.drop_table("communication_logs")
