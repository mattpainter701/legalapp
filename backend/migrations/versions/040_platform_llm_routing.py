"""040 — Platform and premium LLM routing

Revision ID: 040
Revises: 039
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("key", name="uq_platform_settings_key"),
    )
    op.alter_column(
        "tenant_settings",
        "default_llm_model",
        existing_type=sa.String(100),
        type_=sa.String(200),
        existing_nullable=True,
    )
    op.add_column(
        "tenant_settings",
        sa.Column("premium_llm_provider", sa.String(50), nullable=True),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("premium_llm_model", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_settings", "premium_llm_model")
    op.drop_column("tenant_settings", "premium_llm_provider")
    op.alter_column(
        "tenant_settings",
        "default_llm_model",
        existing_type=sa.String(200),
        type_=sa.String(100),
        existing_nullable=True,
    )
    op.drop_table("platform_settings")
