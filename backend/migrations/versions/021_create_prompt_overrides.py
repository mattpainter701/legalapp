"""021 — Create prompt_overrides table for per-tenant prompt customization.

Revision ID: 021
Revises: 020
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_overrides",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("plugin_name", sa.String(100), nullable=False),
        sa.Column("skill_name", sa.String(100), nullable=False),
        sa.Column("prompt_content", sa.Text(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
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
            "tenant_id",
            "plugin_name",
            "skill_name",
            name="uq_prompt_overrides_tenant_plugin_skill",
        ),
    )

    op.create_index(
        "idx_prompt_overrides_tenant",
        "prompt_overrides",
        ["tenant_id"],
    )
    op.create_index(
        "idx_prompt_overrides_lookup",
        "prompt_overrides",
        ["tenant_id", "plugin_name", "skill_name"],
        postgresql_using="hash",
    )

    # RLS
    op.execute("ALTER TABLE prompt_overrides ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE prompt_overrides FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_prompt_overrides ON prompt_overrides"
        " USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_prompt_overrides ON prompt_overrides"
    )
    op.drop_index("idx_prompt_overrides_lookup", table_name="prompt_overrides")
    op.drop_index("idx_prompt_overrides_tenant", table_name="prompt_overrides")
    op.drop_table("prompt_overrides")
