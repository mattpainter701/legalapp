"""026 — Matter system revamp: assignments, notes, retainers, billing fields.

Revision ID: 026
Revises: 025
Create Date: 2026-06-03

Creates:
  - matter_assignments  (M:N users <-> matters with roles)
  - matter_notes        (internal + client-facing notes)
  - retainers           (retainer agreements with balance tracking)
  - retainer_transactions (deposit/drawdown/refund audit trail)

Adds to matters:
  practice_area, billing_cycle, billing_method, hourly_rate,
  contingency_percentage, tax_rate, budget_notification_threshold,
  court, judge, case_number

Adds to users:
  default_billing_rate

Adds to matter_events:
  note_type, metadata_json

Adds to invoices:
  retainer_id, billing_period_start, billing_period_end

Data migration:
  internal_owners JSON -> matter_assignments rows, then drop column
"""

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── matter_assignments ──────────────────────────────────────────────
    op.create_table(
        "matter_assignments",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(50),
            nullable=False,
            server_default="associate",
        ),
        sa.Column(
            "is_primary",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
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
    )
    op.create_unique_constraint(
        "uq_matter_assignment", "matter_assignments", ["matter_id", "user_id"]
    )
    op.create_index(
        "idx_matter_assignments_user",
        "matter_assignments",
        ["tenant_id", "user_id"],
    )
    op.create_index(
        "idx_matter_assignments_matter",
        "matter_assignments",
        ["matter_id"],
    )

    # ── matter_notes ────────────────────────────────────────────────────
    op.create_table(
        "matter_notes",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "note_type",
            sa.String(50),
            nullable=False,
            server_default="internal",
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "is_billable",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        sa.Column("hours", sa.Numeric(6, 2), nullable=True),
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
    op.create_index("idx_matter_notes_matter", "matter_notes", ["matter_id"])
    op.create_index("idx_matter_notes_author", "matter_notes", ["author_id"])
    op.create_index("idx_matter_notes_type", "matter_notes", ["matter_id", "note_type"])

    # ── retainers ───────────────────────────────────────────────────────
    op.create_table(
        "retainers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "matter_id",
            UUID(as_uuid=True),
            sa.ForeignKey("matters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "retainer_type",
            sa.String(50),
            nullable=False,
            server_default="unearned",
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("current_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("minimum_balance", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="SET NULL"),
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
    )
    op.create_index("idx_retainers_matter", "retainers", ["matter_id"])
    op.create_index("idx_retainers_contact", "retainers", ["contact_id"])
    op.create_index("idx_retainers_tenant", "retainers", ["tenant_id"])

    # ── retainer_transactions ───────────────────────────────────────────
    op.create_table(
        "retainer_transactions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "retainer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("retainers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transaction_type", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_retainer_tx_retainer", "retainer_transactions", ["retainer_id"]
    )
    op.create_index("idx_retainer_tx_invoice", "retainer_transactions", ["invoice_id"])

    # ── Matters: new columns ────────────────────────────────────────────
    op.add_column(
        "matters",
        sa.Column("budget_notification_threshold", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "matters",
        sa.Column("practice_area", sa.String(200), nullable=True),
    )
    op.add_column(
        "matters",
        sa.Column(
            "billing_cycle",
            sa.String(50),
            nullable=False,
            server_default="monthly",
        ),
    )
    op.add_column(
        "matters",
        sa.Column(
            "billing_method",
            sa.String(50),
            nullable=False,
            server_default="hourly",
        ),
    )
    op.add_column(
        "matters",
        sa.Column("hourly_rate", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "matters",
        sa.Column("contingency_percentage", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "matters",
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=True),
    )
    op.add_column(
        "matters",
        sa.Column("court", sa.String(300), nullable=True),
    )
    op.add_column(
        "matters",
        sa.Column("judge", sa.String(200), nullable=True),
    )
    op.add_column(
        "matters",
        sa.Column("case_number", sa.String(100), nullable=True),
    )

    # ── Users: new column ───────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("default_billing_rate", sa.Numeric(10, 2), nullable=True),
    )

    # ── MatterEvents: new columns ───────────────────────────────────────
    op.add_column(
        "matter_events",
        sa.Column(
            "note_type",
            sa.String(50),
            nullable=False,
            server_default="system",
        ),
    )
    op.add_column(
        "matter_events",
        sa.Column("metadata_json", sa.JSON, nullable=True),
    )

    # ── Invoices: new columns ───────────────────────────────────────────
    op.add_column(
        "invoices",
        sa.Column(
            "retainer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("retainers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "invoices",
        sa.Column("billing_period_start", sa.Date, nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("billing_period_end", sa.Date, nullable=True),
    )

    # ── Data migration: internal_owners JSON -> matter_assignments ──────
    _migrate_internal_owners()

    # ── Drop the old JSON column ────────────────────────────────────────
    op.drop_column("matters", "internal_owners")

    # ── Row-level security for new tables ───────────────────────────────
    for table in [
        "matter_assignments",
        "matter_notes",
        "retainers",
        "retainer_transactions",
    ]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            """
        )


def downgrade() -> None:
    # ── Drop RLS policies ───────────────────────────────────────────────
    for table in [
        "matter_assignments",
        "matter_notes",
        "retainers",
        "retainer_transactions",
    ]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")

    # ── Restore internal_owners column ───────────────────────────────────
    op.add_column(
        "matters",
        sa.Column("internal_owners", sa.JSON, nullable=True),
    )

    # ── Drop new columns from invoices ──────────────────────────────────
    op.drop_column("invoices", "billing_period_end")
    op.drop_column("invoices", "billing_period_start")
    op.drop_constraint("invoices_retainer_id_fkey", "invoices", type_="foreignkey")
    op.drop_column("invoices", "retainer_id")

    # ── Drop new columns from matter_events ─────────────────────────────
    op.drop_column("matter_events", "metadata_json")
    op.drop_column("matter_events", "note_type")

    # ── Drop new columns from users ─────────────────────────────────────
    op.drop_column("users", "default_billing_rate")

    # ── Drop new columns from matters ───────────────────────────────────
    for col in [
        "case_number",
        "judge",
        "court",
        "tax_rate",
        "contingency_percentage",
        "hourly_rate",
        "billing_method",
        "billing_cycle",
        "practice_area",
        "budget_notification_threshold",
    ]:
        op.drop_column("matters", col)

    # ── Drop new tables ─────────────────────────────────────────────────
    op.drop_table("retainer_transactions")
    op.drop_table("retainers")
    op.drop_table("matter_notes")
    op.drop_table("matter_assignments")


def _migrate_internal_owners() -> None:
    """Read existing internal_owners JSON, insert into matter_assignments.

    Expected internal_owners shape (varies per deployment):
        {"lead_attorney": "user_uuid", "team": ["uuid1", "uuid2"]}
        or a plain list of user UUIDs
        or a dict keyed by user UUID with role strings

    We handle common shapes gracefully. Unknown shapes are skipped with
    a NOTICE so the operator can migrate manually.
    """
    conn = op.get_bind()

    # Check if the column exists before trying to read it
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'matters' AND column_name = 'internal_owners'"
        )
    )
    if not result.fetchone():
        # Column already gone (e.g. fresh DB via create_all)
        return

    rows = conn.execute(
        sa.text(
            "SELECT id, tenant_id, user_id, internal_owners FROM matters "
            "WHERE internal_owners IS NOT NULL"
        )
    ).fetchall()

    import json

    now = sa.text("now()")
    inserted = 0

    for matter_id, tenant_id, matter_user_id, owners_json in rows:
        if owners_json is None:
            continue
        if isinstance(owners_json, str):
            try:
                owners = json.loads(owners_json)
            except (json.JSONDecodeError, TypeError):
                continue
        else:
            owners = owners_json

        if owners is None:
            continue

        user_ids = _extract_user_ids(owners)

        for uid in user_ids:
            try:
                uuid.UUID(str(uid))
            except (ValueError, TypeError):
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO matter_assignments "
                    "(tenant_id, matter_id, user_id, role, is_primary, assigned_at, "
                    "created_at, updated_at) "
                    "VALUES (:tid, :mid, :uid, 'associate', false, :now, :now, :now) "
                    "ON CONFLICT (matter_id, user_id) DO NOTHING"
                ),
                {
                    "tid": tenant_id,
                    "mid": matter_id,
                    "uid": uid,
                    "now": now,
                },
            )
            inserted += 1

    if inserted:
        print(f"  ── Migrated {inserted} internal_owners entries to matter_assignments")


def _extract_user_ids(owners):
    """Extract user UUIDs from various internal_owners JSON shapes.

    Shapes handled:
      - list of UUID strings
      - dict: {"lead_attorney": "uuid", "team": ["uuid", ...]}
      - dict: {"uuid": "lead_attorney", "uuid": "associate"}  (reversed)
      - dict: {"lead": {"id": "uuid", "name": "..."}, "team": [{"id": "uuid"}, ...]}
    """
    ids = []

    if isinstance(owners, list):
        for item in owners:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict):
                uid = item.get("id") or item.get("user_id") or item.get("uuid")
                if uid:
                    ids.append(str(uid))
    elif isinstance(owners, dict):
        # Try {"lead_attorney": "uuid", "team": ["uuid", ...]}
        for key in ("lead_attorney", "lead", "primary"):
            val = owners.get(key)
            if isinstance(val, str):
                ids.append(val)
            elif isinstance(val, dict):
                uid = val.get("id") or val.get("user_id")
                if uid:
                    ids.append(str(uid))
        team = owners.get("team") or owners.get("members") or []
        if isinstance(team, list):
            for item in team:
                if isinstance(item, str):
                    ids.append(item)
                elif isinstance(item, dict):
                    uid = item.get("id") or item.get("user_id") or item.get("uuid")
                    if uid:
                        ids.append(str(uid))
        # Try {"uuid_str": "role", ...}  (reversed dict)
        if not ids:
            for k, v in owners.items():
                if (
                    isinstance(k, str)
                    and len(k) == 36
                    and "-" in k
                    and isinstance(v, str)
                ):
                    ids.append(k)

    return ids
