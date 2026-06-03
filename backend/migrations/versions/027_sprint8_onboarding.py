"""027 — Sprint 8: Tenant onboarding, licensing, cloud folders, customer LLM.

Revision ID: 027
Revises: 026
Create Date: 2026-06-03

Adds to tenants:
  onboarding_completed, onboarding_step, cloud_root_folder, service_account_email

Adds to tenant_credentials:
  granted_by_user_id (FK users.id)

Adds to users:
  license_active

Adds to tenant_settings:
  use_customer_llm, customer_llm_provider, customer_llm_config
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tenants: onboarding + cloud fields ─────────────────────────────
    op.add_column(
        "tenants",
        sa.Column(
            "onboarding_completed",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "onboarding_step",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column("cloud_root_folder", sa.JSON, nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("service_account_email", sa.String(255), nullable=True),
    )

    # ── tenant_credentials: who granted consent ────────────────────────
    op.add_column(
        "tenant_credentials",
        sa.Column(
            "granted_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── users: license tracking ────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column(
            "license_active",
            sa.Boolean,
            nullable=False,
            server_default="true",
        ),
    )

    # ── tenant_settings: customer LLM fields ───────────────────────────
    op.add_column(
        "tenant_settings",
        sa.Column(
            "use_customer_llm",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("customer_llm_provider", sa.String(50), nullable=True),
    )
    op.add_column(
        "tenant_settings",
        sa.Column("customer_llm_config", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_settings", "customer_llm_config")
    op.drop_column("tenant_settings", "customer_llm_provider")
    op.drop_column("tenant_settings", "use_customer_llm")
    op.drop_column("users", "license_active")
    op.drop_constraint(
        "tenant_credentials_granted_by_user_id_fkey",
        "tenant_credentials",
        type_="foreignkey",
    )
    op.drop_column("tenant_credentials", "granted_by_user_id")
    op.drop_column("tenants", "service_account_email")
    op.drop_column("tenants", "cloud_root_folder")
    op.drop_column("tenants", "onboarding_step")
    op.drop_column("tenants", "onboarding_completed")
