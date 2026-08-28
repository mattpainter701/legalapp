"""Persist the user identity behind a matter-scoped client portal account."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "140_client_portal_accounts"
down_revision = "139_agreements_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("client_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_contacts_client_user_id_users",
        "contacts",
        "users",
        ["client_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_contacts_tenant_client_user", "contacts", ["tenant_id", "client_user_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_contacts_tenant_client_user", table_name="contacts")
    op.drop_constraint(
        "fk_contacts_client_user_id_users", "contacts", type_="foreignkey"
    )
    op.drop_column("contacts", "client_user_id")
