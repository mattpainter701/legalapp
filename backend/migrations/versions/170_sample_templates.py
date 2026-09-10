"""170 — Create the platform-owned sample_templates catalog.

The sample library is shared, read-only content: it has no ``tenant_id`` and is
deliberately *not* row-level-security scoped, matching ``platform_settings``.
Every authenticated tenant may read it; writes are performed only by the
operator seeding script (``scripts/seed_sample_templates.py``).

Revision ID: 170_sample_templates
Revises: 169_automation_services
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "170_sample_templates"
down_revision = "169_automation_services"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sample_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="other"),
        sa.Column("jurisdictions", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("format", sa.String(50), nullable=False, server_default="pdf"),
        sa.Column("source_filename", sa.String(500), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_file_size", sa.BigInteger(), nullable=True),
        sa.Column("field_count", sa.Integer(), nullable=True),
        sa.Column("variable_schema", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.CheckConstraint(
            "json_typeof(jurisdictions) IS NULL OR json_typeof(jurisdictions) = 'array'",
            name="ck_sample_templates_jurisdictions_array",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_sample_templates_source_sha256",
        ),
        sa.UniqueConstraint("slug", name="uq_sample_templates_slug"),
    )
    op.create_index(
        "ix_sample_templates_category_active",
        "sample_templates",
        ["category", "is_active"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sample_templates_category_active", table_name="sample_templates"
    )
    op.drop_table("sample_templates")
