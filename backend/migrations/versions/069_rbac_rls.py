"""RLS for RBAC roles/user_roles: denormalize tenant_id + tenant_isolation + bypass.

Revision ID: 069
Revises: 068
Create Date: 2026-06-23
"""

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "069"
down_revision: Union[str, None] = "068"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Denormalize tenant_id onto user_roles so it can carry a direct RLS policy.
    op.add_column(
        "user_roles",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE user_roles ur SET tenant_id = r.tenant_id
        FROM roles r WHERE r.id = ur.role_id
        """
    )
    op.alter_column("user_roles", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_user_roles_tenant",
        "user_roles",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("idx_user_roles_tenant_id", "user_roles", ["tenant_id"])

    # Two-policy RLS pair on both tables, mirroring the users table:
    #   <table>_tenant_isolation  — normal requests (app.current_tenant_id)
    #   <table>_rls_bypass        — auth/registration paths (app.rls_bypass='on')
    for table in ("roles", "user_roles"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
            )
            WITH CHECK (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
            )
            """
        )
        op.execute(f"DROP POLICY IF EXISTS {table}_rls_bypass ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_rls_bypass ON {table}
            FOR ALL TO PUBLIC
            USING (current_setting('app.rls_bypass', true) = 'on')
            """
        )


def downgrade() -> None:
    for table in ("roles", "user_roles"):
        op.execute(f"DROP POLICY IF EXISTS {table}_rls_bypass ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("idx_user_roles_tenant_id", table_name="user_roles")
    op.drop_constraint("fk_user_roles_tenant", "user_roles", type_="foreignkey")
    op.drop_column("user_roles", "tenant_id")
