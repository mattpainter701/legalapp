"""029 — Tenant LLM provider: default_llm_provider, default_llm_model

Revision ID: 029
Revises: 028
Create Date: 2026-06-03

Adds to tenant_settings:
  default_llm_provider — which platform LLM to use (deepseek|opencode|openrouter|anthropic|azure|gemini)
  default_llm_model    — optional model override (e.g. openrouter free model)
"""

from alembic import op
import sqlalchemy as sa

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_settings",
        sa.Column("default_llm_provider", sa.String(50), nullable=True),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("default_llm_model", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_settings", "default_llm_model")
    op.drop_column("tenant_settings", "default_llm_provider")
