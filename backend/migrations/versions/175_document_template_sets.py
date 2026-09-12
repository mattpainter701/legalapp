"""175 — Sets: the templates a firm drafts together.

Two tenant-scoped tables under the same RLS posture as the rest of the Studio:
row-level security enabled and forced, with a tenant_isolation policy in the
NULLIF form 173 established, so a lost tenant context matches no rows instead
of raising.

A set references templates; it never copies them. Deleting a template removes
it from every set that named it rather than leaving a member that cannot be
drafted.

Revision ID: 175_document_template_sets
Revises: 174_firm_currency
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "175_document_template_sets"
down_revision = "174_firm_currency"
branch_labels = None
depends_on = None

TABLES = ("document_template_sets", "document_template_set_items")

_TENANT = "nullif(current_setting('app.current_tenant_id',true),'')::uuid"


def upgrade() -> None:
    op.create_table(
        "document_template_sets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("module", sa.String(50), nullable=True),
        sa.Column("jurisdiction", sa.String(100), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.UniqueConstraint("tenant_id", "title", name="uq_document_template_sets_title"),
    )
    op.create_index(
        "ix_document_template_sets_tenant", "document_template_sets", ["tenant_id"]
    )

    # A composite foreign key needs a unique constraint on the columns it
    # references. Migration 146 added the same shape to ``matters`` so research
    # workspaces could be tenant-locked at the database level; this does it for
    # templates so a set can never name another tenant's template.
    op.create_unique_constraint(
        "uq_document_templates_tenant_id", "document_templates", ["tenant_id", "id"]
    )

    op.create_table(
        "document_template_set_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("pinned_version_no", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["set_id"], ["document_template_sets.id"], ondelete="CASCADE"
        ),
        # Composite, so a set can only ever name a template of its own tenant.
        sa.ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            ["document_templates.tenant_id", "document_templates.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "set_id", "template_id", name="uq_document_template_set_items_template"
        ),
        sa.UniqueConstraint(
            "set_id", "position", name="uq_document_template_set_items_position"
        ),
        sa.CheckConstraint("position >= 0", name="ck_document_template_set_items_position"),
        sa.CheckConstraint(
            "pinned_version_no IS NULL OR pinned_version_no > 0",
            name="ck_document_template_set_items_version",
        ),
    )
    op.create_index(
        "ix_document_template_set_items_set",
        "document_template_set_items",
        ["tenant_id", "set_id"],
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id={_TENANT}) WITH CHECK (tenant_id={_TENANT})"
        )


def downgrade() -> None:
    # A set is firm configuration, not evidence: nothing here records what was
    # filed, so dropping it loses convenience rather than history.
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_index(
        "ix_document_template_set_items_set", table_name="document_template_set_items"
    )
    op.drop_table("document_template_set_items")
    op.drop_index("ix_document_template_sets_tenant", table_name="document_template_sets")
    op.drop_table("document_template_sets")
    op.drop_constraint(
        "uq_document_templates_tenant_id", "document_templates", type_="unique"
    )
