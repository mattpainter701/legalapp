"""Static safety contracts for the platform background-quota RLS boundary.

The database fixture builds tables from SQLAlchemy metadata and therefore does
not apply Alembic policies.  Keep these checks source-based and focused: they
ensure the migration cannot accidentally turn the quota ledger into a broad
RLS bypass while still allowing the service's explicitly-selected platform
scope to inspect the shared pool.
"""

from pathlib import Path
import re


_MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "versions" / "134_background_ai_quota.py"
)
_SERVICE = Path(__file__).parents[1] / "app" / "services" / "background_ai_quota.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_background_quota_migration_forces_rls_and_has_no_broad_bypass():
    source = _source(_MIGRATION)

    assert "background_ai_usage_reservations" in source
    assert re.search(
        r"alter\s+table\s+background_ai_usage_reservations\s+enable\s+row\s+level\s+security",
        source,
    )
    assert re.search(
        r"alter\s+table\s+background_ai_usage_reservations\s+force\s+row\s+level\s+security",
        source,
    )
    # This ledger must not inherit the application's broad auth escape hatch.
    assert "app.rls_bypass" not in source


def test_background_quota_policies_require_tenant_or_explicit_platform_scope():
    source = _source(_MIGRATION)

    assert "create policy" in source
    assert "current_setting('app.current_tenant_id', true)" in source
    assert "app.background_ai_quota_scope" in source
    assert "current_setting('app.background_ai_quota_scope', true)" in source
    # Scope is an exact, transaction-local opt-in—not a truthy/non-empty value.
    assert re.search(
        r"current_setting\('app\.background_ai_quota_scope',\s*true\)\s*=\s*'on'",
        source,
    )


def test_background_quota_service_selects_scope_without_rls_bypass():
    source = _source(_SERVICE)

    assert 'background_quota_scope_guc = "app.background_ai_quota_scope"' in source
    assert "set_config" in source
    assert not re.search(r"set_config\([^)]*app\.rls_bypass", source)
    assert re.search(r"set_config\([^)]*'on',\s*true\)", source)
