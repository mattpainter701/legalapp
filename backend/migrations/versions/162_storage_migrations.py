"""Auditable provider reconciliation and tenant cloud root history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "162_storage_migrations"
down_revision = "161_user_alias_addresses"
branch_labels = None
depends_on = None


def _timestamp(name, nullable=True):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=None if nullable else sa.text("now()"))


def upgrade():
    op.create_table(
        "storage_migrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_provider", sa.String(50), nullable=False),
        sa.Column("target_provider", sa.String(50), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        _timestamp("started_at", False), _timestamp("completed_at"),
        sa.Column("operator_id", UUID(as_uuid=True)),
        sa.Column("evidence_version", sa.String(200)),
        sa.Column("acknowledged_policy", sa.String(100)),
        sa.Column("previous_root", sa.JSON()), sa.Column("target_root", sa.JSON()),
        sa.Column("bucket_counts", sa.JSON(), nullable=False),
        sa.Column("needs_reindex", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.Text()),
        _timestamp("created_at", False), _timestamp("updated_at", False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_storage_migrations_tenant_id"),
        sa.CheckConstraint("phase IN ('planning','reconciling','awaiting_confirmation','cutover','complete','abandoned')", name="ck_storage_migration_phase"),
    )
    op.create_index("idx_storage_migrations_tenant_phase", "storage_migrations", ["tenant_id", "phase"])
    op.create_index("uq_storage_migrations_one_active_tenant", "storage_migrations", ["tenant_id"], unique=True, postgresql_where=sa.text("phase IN ('planning','reconciling','awaiting_confirmation','cutover')"))
    op.create_table(
        "storage_migration_matches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("migration_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("object_type", sa.String(30), nullable=False),
        sa.Column("object_id", sa.String(500), nullable=False),
        sa.Column("bucket", sa.String(30), nullable=False),
        sa.Column("matching_rung", sa.String(30)),
        sa.Column("source_ref", sa.JSON()), sa.Column("target_ref", sa.JSON()), sa.Column("evidence", sa.JSON()),
        _timestamp("created_at", False),
        sa.ForeignKeyConstraint(["tenant_id", "migration_id"], ["storage_migrations.tenant_id", "storage_migrations.id"], ondelete="CASCADE", name="fk_storage_matches_tenant_migration"),
        sa.UniqueConstraint("migration_id", "object_type", "object_id", name="uq_storage_match_object"),
        sa.CheckConstraint("bucket IN ('matched','missing','ambiguous')", name="ck_storage_match_bucket"),
        sa.CheckConstraint("object_type IN ('matter','document')", name="ck_storage_match_type"),
    )
    op.create_index("idx_storage_migration_matches_migration_bucket", "storage_migration_matches", ["migration_id", "bucket"])
    op.create_table(
        "onboarding_root_audits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("root", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True)),
        _timestamp("created_at", False),
    )
    predicate = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
    for table in ("storage_migrations", "storage_migration_matches", "onboarding_root_audits"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})")


def downgrade():
    op.drop_table("onboarding_root_audits")
    op.drop_table("storage_migration_matches")
    op.drop_table("storage_migrations")
