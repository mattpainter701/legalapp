"""RBAC roles and user_roles, seed system roles, backfill from users.role.

Revision ID: 068
Revises: 067
Create Date: 2026-06-23
"""

import json
import uuid
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


SYSTEM_ROLE_CAPABILITIES = {
    "Administrator": [
        "manage_users",
        "manage_roles",
        "manage_billing",
        "view_billing",
        "manage_matters",
        "manage_intake",
        "manage_documents",
        "manage_integrations",
        "admin_settings",
        "use_premium_ai",
    ],
    "Accountant": ["view_billing", "manage_billing"],
    "User": ["manage_matters", "manage_intake", "manage_documents"],
    "Client": [],
}
LEGACY_ROLE_TO_SYSTEM_ROLE = {
    "admin": "Administrator",
    "accountant": "Accountant",
    "user": "User",
    "client": "Client",
}


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "capabilities",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )
    op.create_index("idx_roles_tenant_id", "roles", ["tenant_id"])

    op.create_table(
        "user_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )
    op.create_index("idx_user_roles_user_id", "user_roles", ["user_id"])
    op.create_index("idx_user_roles_role_id", "user_roles", ["role_id"])

    bind = op.get_bind()
    tenants = bind.execute(sa.text("SELECT id FROM tenants")).fetchall()
    for (tenant_id,) in tenants:
        name_to_role_id: dict[str, str] = {}
        for role_name, caps in SYSTEM_ROLE_CAPABILITIES.items():
            role_id = str(uuid.uuid4())
            name_to_role_id[role_name] = role_id
            bind.execute(
                sa.text(
                    "INSERT INTO roles (id, tenant_id, name, description, "
                    "capabilities, is_system) VALUES (:id, :tid, :name, :descr, "
                    "CAST(:caps AS jsonb), true)"
                ),
                {
                    "id": role_id,
                    "tid": str(tenant_id),
                    "name": role_name,
                    "descr": f"System role: {role_name}",
                    "caps": json.dumps(caps),
                },
            )
        users = bind.execute(
            sa.text("SELECT id, role FROM users WHERE tenant_id = :tid"),
            {"tid": str(tenant_id)},
        ).fetchall()
        for user_id, legacy_role in users:
            system_name = LEGACY_ROLE_TO_SYSTEM_ROLE.get(
                (legacy_role or "user"), "User"
            )
            bind.execute(
                sa.text(
                    "INSERT INTO user_roles (id, user_id, role_id, source) "
                    "VALUES (:id, :uid, :rid, 'manual')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "uid": str(user_id),
                    "rid": name_to_role_id[system_name],
                },
            )


def downgrade() -> None:
    op.drop_table("user_roles")
    op.drop_table("roles")
