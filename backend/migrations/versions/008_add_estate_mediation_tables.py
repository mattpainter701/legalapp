"""Add estate and mediation tables for Trust & Estate and Mediation plugins.

Revision ID: 008
Revises: 007
Create Date: 2026-06-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. estates ────────────────────────────────────────────────────────────
    op.create_table(
        "estates",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("grantor", sa.String(300), nullable=True),
        sa.Column("estate_type", sa.String(100), nullable=True),
        sa.Column("status", sa.String(100), nullable=False, server_default="active"),
        sa.Column("summary", sa.Text(), nullable=True),
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
    op.create_index("ix_estates_tenant_id", "estates", ["tenant_id"])

    # ── 2. estate_events ──────────────────────────────────────────────────────
    op.create_table(
        "estate_events",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "estate_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("estates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False, server_default="other"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_estate_events_estate_id", "estate_events", ["estate_id"])

    # ── 3. mediation_cases ────────────────────────────────────────────────────
    op.create_table(
        "mediation_cases",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("parties", sa.String(500), nullable=True),
        sa.Column("status", sa.String(100), nullable=False, server_default="active"),
        sa.Column("summary", sa.Text(), nullable=True),
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
    op.create_index("ix_mediation_cases_tenant_id", "mediation_cases", ["tenant_id"])

    # ── 4. mediation_case_events ──────────────────────────────────────────────
    op.create_table(
        "mediation_case_events",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "case_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("mediation_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False, server_default="other"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_mediation_case_events_case_id", "mediation_case_events", ["case_id"]
    )

    # ── 5. Row-Level Security ─────────────────────────────────────────────────

    # estates
    op.execute("ALTER TABLE estates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE estates FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_estates ON estates
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    # estate_events (inherits tenant isolation via CASCADE FK to estates)
    op.execute("ALTER TABLE estate_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE estate_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_estate_events ON estate_events
        FOR ALL TO PUBLIC
        USING (
            estate_id IN (
                SELECT id FROM estates
                WHERE tenant_id::text = current_setting('app.current_tenant_id', true)
            )
        )
        """
    )

    # mediation_cases
    op.execute("ALTER TABLE mediation_cases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mediation_cases FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_mediation_cases ON mediation_cases
        FOR ALL TO PUBLIC
        USING (
            tenant_id::text = current_setting('app.current_tenant_id', true)
        )
        """
    )

    # mediation_case_events (inherits tenant isolation via CASCADE FK)
    op.execute("ALTER TABLE mediation_case_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mediation_case_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_mediation_case_events ON mediation_case_events
        FOR ALL TO PUBLIC
        USING (
            case_id IN (
                SELECT id FROM mediation_cases
                WHERE tenant_id::text = current_setting('app.current_tenant_id', true)
            )
        )
        """
    )


def downgrade() -> None:
    for table in (
        "mediation_case_events",
        "mediation_cases",
        "estate_events",
        "estates",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("mediation_case_events")
    op.drop_table("mediation_cases")
    op.drop_table("estate_events")
    op.drop_table("estates")
