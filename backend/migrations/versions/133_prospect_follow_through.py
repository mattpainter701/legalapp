"""Add after-call prospect follow-through and engagement packet storage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "133_prospect_follow_through"
down_revision = "132_smb_agent_lifecycle_indexes"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)""")


def upgrade() -> None:
    op.create_table(
        "prospect_follow_through",
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
            "lead_id",
            UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "intake_communication_id",
            UUID(as_uuid=True),
            sa.ForeignKey("communication_logs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "primary_task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column(
            "assigned_attorney_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status", sa.String(40), nullable=False, server_default="attorney_review"
        ),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("next_action_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
        sa.UniqueConstraint(
            "tenant_id", "lead_id", name="uq_prospect_follow_through_lead"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_prospect_follow_through_idempotency",
        ),
        sa.CheckConstraint(
            "lead_id IS NOT NULL OR contact_id IS NOT NULL",
            name="ck_prospect_follow_through_subject",
        ),
    )
    op.create_index(
        "idx_prospect_follow_through_tenant_status",
        "prospect_follow_through",
        ["tenant_id", "status"],
    )
    op.create_index(
        "idx_prospect_follow_through_tenant_next_action",
        "prospect_follow_through",
        ["tenant_id", "next_action_due_at"],
    )
    op.create_index(
        "uq_prospect_follow_through_contact",
        "prospect_follow_through",
        ["tenant_id", "contact_id"],
        unique=True,
        postgresql_where=sa.text("lead_id IS NULL"),
    )
    op.create_table(
        "engagement_packets",
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
            "prospect_id",
            UUID(as_uuid=True),
            sa.ForeignKey("prospect_follow_through.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("packet_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column(
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("document_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("inputs", JSONB(), nullable=False, server_default="{}"),
        sa.Column("prepared_content", JSONB(), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
        sa.UniqueConstraint(
            "tenant_id", "prospect_id", "packet_type", name="uq_engagement_packets_kind"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "prospect_id",
            "idempotency_key",
            name="uq_engagement_packets_idempotency",
        ),
    )
    op.create_index(
        "idx_engagement_packets_tenant_prospect",
        "engagement_packets",
        ["tenant_id", "prospect_id", "created_at"],
    )
    op.create_index(
        "idx_engagement_packets_tenant_status",
        "engagement_packets",
        ["tenant_id", "status"],
    )
    op.create_table(
        "prospect_contact_events",
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
            "prospect_id",
            UUID(as_uuid=True),
            sa.ForeignKey("prospect_follow_through.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "communication_id",
            UUID(as_uuid=True),
            sa.ForeignKey("communication_logs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
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
        sa.UniqueConstraint(
            "tenant_id",
            "prospect_id",
            "idempotency_key",
            name="uq_prospect_contact_events_idempotency",
        ),
    )
    op.create_index(
        "idx_prospect_contact_events_tenant_prospect",
        "prospect_contact_events",
        ["tenant_id", "prospect_id", "occurred_at"],
    )
    op.create_table(
        "prospect_follow_through_events",
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
            "prospect_id",
            UUID(as_uuid=True),
            sa.ForeignKey("prospect_follow_through.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(40), nullable=True),
        sa.Column("to_status", sa.String(40), nullable=True),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_prospect_follow_through_events_tenant_prospect",
        "prospect_follow_through_events",
        ["tenant_id", "prospect_id", "created_at"],
    )
    for table in (
        "prospect_follow_through",
        "engagement_packets",
        "prospect_contact_events",
        "prospect_follow_through_events",
    ):
        _rls(table)


def downgrade() -> None:
    for table in (
        "prospect_follow_through_events",
        "prospect_contact_events",
        "engagement_packets",
        "prospect_follow_through",
    ):
        op.drop_table(table)
