"""Persistent matter intake packets, delivery claims and independent requirements."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "158_matter_intakes"
down_revision = "157_template_pub_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_signature_requests_tenant_id", "signature_requests", ["tenant_id", "id"]
    )
    op.create_unique_constraint(
        "uq_client_portal_invites_tenant_id",
        "client_portal_invites",
        ["tenant_id", "id"],
    )
    op.create_table(
        "matter_intakes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *[
            sa.Column(name, UUID(as_uuid=True), nullable=False)
            for name in ("matter_id", "contact_id", "owner_id", "created_by")
        ],
        sa.Column(
            "signature_id",
            UUID(as_uuid=True),
            sa.ForeignKey("signature_requests.id"),
            nullable=False,
        ),
        sa.Column(
            "invite_id",
            UUID(as_uuid=True),
            sa.ForeignKey("client_portal_invites.id"),
            nullable=False,
        ),
        sa.Column("encrypted_invite", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        *[
            sa.Column(name, JSONB(), nullable=False)
            for name in ("config", "requirements", "answers", "delivery")
        ],
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("meeting", JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "matter_id", name="uq_matter_intakes_matter"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"], ["contacts.tenant_id", "contacts.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"], ["users.tenant_id", "users.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "signature_id"],
            ["signature_requests.tenant_id", "signature_requests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invite_id"],
            ["client_portal_invites.tenant_id", "client_portal_invites.id"],
        ),
    )
    op.create_index(
        "ix_matter_intakes_tenant_status", "matter_intakes", ["tenant_id", "status"]
    )
    op.execute("ALTER TABLE matter_intakes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE matter_intakes FORCE ROW LEVEL SECURITY")
    predicate = (
        "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
    )
    op.execute(
        f"CREATE POLICY matter_intakes_tenant_isolation ON matter_intakes USING ({predicate}) WITH CHECK ({predicate})"
    )


def downgrade():
    op.drop_table("matter_intakes")
    op.drop_constraint(
        "uq_signature_requests_tenant_id", "signature_requests", type_="unique"
    )
    op.drop_constraint(
        "uq_client_portal_invites_tenant_id", "client_portal_invites", type_="unique"
    )
