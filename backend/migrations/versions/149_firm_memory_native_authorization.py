"""Firm Memory native identity and authorization state.

Revision ID: 149_fm_native_authz
Revises: 148_configurable_workflows
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "149_fm_native_authz"
down_revision = "148_configurable_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "native_identity_mappings",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("directory_tenant_id", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("primary_sid", sa.String(184), nullable=False),
        sa.Column(
            "effective_sids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "tenant_id", "user_id", name="uq_native_identity_tenant_user"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "directory_tenant_id",
            "object_id",
            name="uq_native_identity_directory_object",
        ),
    )
    op.create_index(
        "ix_native_identity_tenant_state",
        "native_identity_mappings",
        ["tenant_id", "state"],
    )
    op.execute("ALTER TABLE native_identity_mappings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE native_identity_mappings FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY native_identity_mappings_tenant_isolation
      ON native_identity_mappings USING (
        tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
      ) WITH CHECK (
        tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
      )""")


def downgrade() -> None:
    op.drop_table("native_identity_mappings")
