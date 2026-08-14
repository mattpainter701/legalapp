"""018 — Create contacts table; add client_contact_id to matters.

Revision ID: 018
Revises: 017
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "contacts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "entity_type", sa.String(50), nullable=False, server_default="person"
        ),
        sa.Column(
            "contact_type", sa.String(50), nullable=False, server_default="client"
        ),
        sa.Column("first_name", sa.String(200), nullable=True),
        sa.Column("last_name", sa.String(200), nullable=True),
        sa.Column("organization_name", sa.String(500), nullable=True),
        sa.Column("email", sa.String(300), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("secondary_phone", sa.String(50), nullable=True),
        sa.Column("address", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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

    op.create_index("idx_contacts_tenant_id", "contacts", ["tenant_id"])
    op.create_index("idx_contacts_tenant_email", "contacts", ["tenant_id", "email"])
    op.create_index(
        "idx_contacts_tenant_last_name", "contacts", ["tenant_id", "last_name"]
    )

    # RLS
    op.execute("ALTER TABLE contacts ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY contacts_tenant_isolation ON contacts
        USING (tenant_id = current_setting('app.tenant_id')::uuid)
    """)

    # Add nullable FK from matters to contacts
    op.add_column(
        "matters",
        sa.Column(
            "client_contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("idx_matters_client_contact_id", "matters", ["client_contact_id"])


def downgrade() -> None:
    op.drop_index("idx_matters_client_contact_id", "matters")
    op.drop_column("matters", "client_contact_id")
    op.execute("DROP POLICY IF EXISTS contacts_tenant_isolation ON contacts")
    op.drop_table("contacts")
