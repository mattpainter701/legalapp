"""Add client account relationships and structured contact preferences.

Revision ID: 112_client_account_relationships
Revises: 111_client_crm_management
"""

from alembic import op
import sqlalchemy as sa


revision = "112_client_account_relationships"
down_revision = "111_client_crm_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("client_since", sa.Date(), nullable=True))
    op.add_column(
        "contacts", sa.Column("preferred_contact_window", sa.String(200), nullable=True)
    )
    op.add_column(
        "contacts",
        sa.Column("preferred_contact_timezone", sa.String(100), nullable=True),
    )
    op.add_column("contacts", sa.Column("client_account_id", sa.UUID(), nullable=True))
    op.add_column(
        "contacts", sa.Column("client_contact_role", sa.String(200), nullable=True)
    )
    op.add_column(
        "contacts",
        sa.Column(
            "is_primary_client_contact",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "contacts", sa.Column("client_contact_authorization", sa.Text(), nullable=True)
    )
    op.create_foreign_key(
        "fk_contacts_client_account_id_contacts",
        "contacts",
        "contacts",
        ["client_account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_contacts_tenant_client_account",
        "contacts",
        ["tenant_id", "client_account_id"],
    )

    op.execute("ALTER TABLE contacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE contacts FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("idx_contacts_tenant_client_account", table_name="contacts")
    op.drop_constraint(
        "fk_contacts_client_account_id_contacts", "contacts", type_="foreignkey"
    )
    for column in (
        "client_contact_authorization",
        "is_primary_client_contact",
        "client_contact_role",
        "client_account_id",
        "preferred_contact_timezone",
        "preferred_contact_window",
        "client_since",
    ):
        op.drop_column("contacts", column)
