"""Offline contracts for the portability migrations."""

from importlib import import_module
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def _render(module_name, direction="upgrade"):
    module = import_module(f"migrations.versions.{module_name}")
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    original = module.op
    module.op = Operations(context)
    try:
        getattr(module, direction)()
    finally:
        module.op = original
    return output.getvalue().lower()


def test_portability_revisions_are_linear():
    revisions = [
        import_module(f"migrations.versions.{name}")
        for name in ("160_cloud_provider_tiers", "161_user_alias_addresses", "162_storage_migrations")
    ]
    assert [m.revision for m in revisions] == [
        "160_cloud_provider_tiers", "161_user_alias_addresses", "162_storage_migrations"
    ]
    assert revisions[0].down_revision == "159_navigation_profiles"
    assert revisions[1].down_revision == revisions[0].revision
    assert revisions[2].down_revision == revisions[1].revision


def test_tier_migration_clips_legacy_preview_in_offline_sql():
    sql = _render("160_cloud_provider_tiers")
    assert "left(snippet, 500)" in sql
    assert "account_type" in sql
    assert "where char_length(snippet) > 500" in sql
    assert "check (snippet is null or char_length(snippet) <= 500)" in sql
    assert "alter column snippet type" not in sql


def test_alias_migration_has_rls_and_read_only_verified_lookup():
    sql = _render("161_user_alias_addresses")
    assert "enable row level security" in sql
    assert "force row level security" in sql
    assert "is_verified" in sql
    assert "for select" in sql
    assert "rls_bypass" in sql


def test_storage_migration_schema_has_constraints_rls_and_active_unique():
    sql = _render("162_storage_migrations")
    assert "create table storage_migrations" in sql
    assert "create table storage_migration_matches" in sql
    assert "create table onboarding_root_audits" in sql
    assert "uq_storage_migrations_one_active_tenant" in sql
    assert "phase in ('planning','reconciling','awaiting_confirmation','cutover')" in sql
    assert "fk_storage_matches_tenant_migration" in sql
    assert sql.count("force row level security") == 3


def test_storage_models_expose_migration_contract():
    from app.models.storage_migration import StorageMigration, StorageMigrationMatch, OnboardingRootAudit

    assert {"tenant_id", "source_provider", "target_provider", "phase", "evidence_version", "bucket_counts", "needs_reindex"} <= set(StorageMigration.__table__.columns.keys())
    assert {"migration_id", "tenant_id", "object_type", "object_id", "bucket", "matching_rung"} <= set(StorageMigrationMatch.__table__.columns.keys())
    assert {"tenant_id", "root", "action", "actor_id"} <= set(OnboardingRootAudit.__table__.columns.keys())
