"""Create provider-neutral external import staging tables.

Revision ID: 062
Revises: 061
Create Date: 2026-06-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "062"
down_revision: Union[str, None] = "061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (
            tenant_id = current_setting('app.current_tenant_id', true)::uuid
        )
        WITH CHECK (
            tenant_id = current_setting('app.current_tenant_id', true)::uuid
        )
        """
    )


def upgrade() -> None:
    op.create_table(
        "external_system_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("external_key", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(300), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("accounting_mode", sa.String(50), nullable=False, server_default="tabs3_reference"),
        sa.Column("source_metadata", JSONB(), nullable=True),
        sa.Column("last_import_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("last_import_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "provider", "external_key", name="uq_external_system_connections_source"),
    )
    op.create_index("idx_external_system_connections_tenant", "external_system_connections", ["tenant_id"])
    _enable_rls("external_system_connections")

    op.create_table(
        "external_import_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", UUID(as_uuid=True), sa.ForeignKey("external_system_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("source_system", sa.String(200), nullable=True),
        sa.Column("export_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="uploaded"),
        sa.Column("manifest", JSONB(), nullable=True),
        sa.Column("row_counts", JSONB(), nullable=True),
        sa.Column("checksum_summary", JSONB(), nullable=True),
        sa.Column("warnings", JSONB(), nullable=True),
        sa.Column("errors", JSONB(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_external_import_runs_tenant", "external_import_runs", ["tenant_id"])
    op.create_index("idx_external_import_runs_connection", "external_import_runs", ["connection_id"])
    op.create_index("idx_external_import_runs_status", "external_import_runs", ["tenant_id", "status"])
    _enable_rls("external_import_runs")

    op.create_table(
        "external_raw_rows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("import_run_id", UUID(as_uuid=True), sa.ForeignKey("external_import_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("source_table", sa.String(100), nullable=False),
        sa.Column("source_row_key", sa.String(300), nullable=False),
        sa.Column("row_checksum", sa.String(64), nullable=False),
        sa.Column("row_data", JSONB(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("import_run_id", "source_table", "source_row_key", name="uq_external_raw_rows_run_table_key"),
    )
    op.create_index("idx_external_raw_rows_tenant", "external_raw_rows", ["tenant_id"])
    op.create_index("idx_external_raw_rows_table", "external_raw_rows", ["tenant_id", "provider", "source_table"])
    op.create_index("idx_external_raw_rows_run", "external_raw_rows", ["import_run_id"])
    _enable_rls("external_raw_rows")

    op.create_table(
        "external_record_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("source_table", sa.String(100), nullable=False),
        sa.Column("source_row_key", sa.String(300), nullable=False),
        sa.Column("import_run_id", UUID(as_uuid=True), sa.ForeignKey("external_import_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_table", sa.String(100), nullable=False),
        sa.Column("target_record_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="linked"),
        sa.Column("confidence", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "source_table",
            "source_row_key",
            "target_table",
            name="uq_external_record_links_source_target",
        ),
    )
    op.create_index("idx_external_record_links_tenant", "external_record_links", ["tenant_id"])
    op.create_index("idx_external_record_links_source", "external_record_links", ["tenant_id", "provider", "source_table"])
    op.create_index("idx_external_record_links_target", "external_record_links", ["tenant_id", "target_table", "target_record_id"])
    _enable_rls("external_record_links")


def downgrade() -> None:
    for table in (
        "external_record_links",
        "external_raw_rows",
        "external_import_runs",
        "external_system_connections",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")

    op.drop_index("idx_external_record_links_target", table_name="external_record_links")
    op.drop_index("idx_external_record_links_source", table_name="external_record_links")
    op.drop_index("idx_external_record_links_tenant", table_name="external_record_links")
    op.drop_table("external_record_links")

    op.drop_index("idx_external_raw_rows_run", table_name="external_raw_rows")
    op.drop_index("idx_external_raw_rows_table", table_name="external_raw_rows")
    op.drop_index("idx_external_raw_rows_tenant", table_name="external_raw_rows")
    op.drop_table("external_raw_rows")

    op.drop_index("idx_external_import_runs_status", table_name="external_import_runs")
    op.drop_index("idx_external_import_runs_connection", table_name="external_import_runs")
    op.drop_index("idx_external_import_runs_tenant", table_name="external_import_runs")
    op.drop_table("external_import_runs")

    op.drop_index("idx_external_system_connections_tenant", table_name="external_system_connections")
    op.drop_table("external_system_connections")
