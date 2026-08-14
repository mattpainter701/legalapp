"""Add plugin system tables: practice_profiles, matters, matter_events, renewals.

Revision ID: 002
Revises: 001
Create Date: 2024-01-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. practice_profiles ──────────────────────────────────────────────────
    op.create_table(
        "practice_profiles",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plugin_name", sa.String(100), nullable=False),
        sa.Column("profile_content", sa.Text(), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("setup_step", sa.Integer(), nullable=False, server_default="0"),
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
            "tenant_id", "plugin_name", name="uq_practice_profiles_tenant_plugin"
        ),
    )
    op.create_index(
        "ix_practice_profiles_tenant_id", "practice_profiles", ["tenant_id"]
    )

    # ── 2. matters ────────────────────────────────────────────────────────────
    op.create_table(
        "matters",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("matter_name", sa.String(500), nullable=False),
        sa.Column("matter_type", sa.String(100), nullable=False),
        sa.Column("role", sa.String(100), nullable=False),
        sa.Column("counterparty", sa.String(500), nullable=False),
        sa.Column("jurisdiction", sa.String(300), nullable=False),
        sa.Column(
            "status", sa.String(100), nullable=False, server_default="threatened"
        ),
        sa.Column("stage", sa.String(200), nullable=True),
        sa.Column("source", sa.String(500), nullable=True),
        sa.Column("risk_level", sa.String(50), nullable=True),
        sa.Column("materiality", sa.String(50), nullable=True),
        sa.Column("exposure_range", sa.String(200), nullable=True),
        sa.Column("outside_counsel", sa.JSON(), nullable=True),
        sa.Column("internal_owners", sa.JSON(), nullable=True),
        sa.Column(
            "conflicts_status", sa.String(50), nullable=False, server_default="not-run"
        ),
        sa.Column("conflicts_override_reason", sa.Text(), nullable=True),
        sa.Column(
            "legal_hold_issued", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("legal_hold_details", sa.JSON(), nullable=True),
        sa.Column("key_dates", sa.JSON(), nullable=True),
        sa.Column("initial_posture", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(50), nullable=True),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("outcome", sa.String(200), nullable=True),
        sa.Column("final_cost", sa.String(100), nullable=True),
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
        sa.UniqueConstraint("tenant_id", "slug", name="uq_matters_tenant_slug"),
    )
    op.create_index("ix_matters_tenant_id", "matters", ["tenant_id"])
    op.create_index("ix_matters_user_id", "matters", ["user_id"])
    op.create_index("ix_matters_status", "matters", ["status"])

    # ── 3. matter_events ──────────────────────────────────────────────────────
    op.create_table(
        "matter_events",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "matter_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_by",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_matter_events_matter_id", "matter_events", ["matter_id"])
    op.create_index("ix_matter_events_tenant_id", "matter_events", ["tenant_id"])

    # ── 4. renewals ───────────────────────────────────────────────────────────
    op.create_table(
        "renewals",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contract_name", sa.String(500), nullable=False),
        sa.Column("vendor", sa.String(300), nullable=False),
        sa.Column("contract_value_annual", sa.Numeric(12, 2), nullable=True),
        sa.Column("renewal_date", sa.Date(), nullable=False),
        sa.Column("notice_deadline", sa.Date(), nullable=True),
        sa.Column("auto_renewal", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("price_increase_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("business_owner", sa.String(300), nullable=True),
        sa.Column("business_owner_email", sa.String(300), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("decision_deadline", sa.Date(), nullable=True),
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
    )
    op.create_index("ix_renewals_tenant_id", "renewals", ["tenant_id"])
    op.create_index("ix_renewals_renewal_date", "renewals", ["renewal_date"])

    # ── 5. Row-Level Security ─────────────────────────────────────────────────

    # practice_profiles
    op.execute("ALTER TABLE practice_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE practice_profiles FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_practice_profiles ON practice_profiles
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    # matters
    op.execute("ALTER TABLE matters ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE matters FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_matters ON matters
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    # matter_events
    op.execute("ALTER TABLE matter_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE matter_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_matter_events ON matter_events
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    # renewals
    op.execute("ALTER TABLE renewals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE renewals FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_renewals ON renewals
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )


def downgrade() -> None:
    for table in ("renewals", "matter_events", "matters", "practice_profiles"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("renewals")
    op.drop_table("matter_events")
    op.drop_table("matters")
    op.drop_table("practice_profiles")
