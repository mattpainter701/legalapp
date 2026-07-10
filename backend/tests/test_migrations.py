from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_graph_resolves_heads():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()

    assert heads == ["090_zoom_account_binding"]


def test_zoom_phone_migration_is_fail_closed_and_restores_force_rls():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "089_zoom_phone_durability.py"
    ).read_text(encoding="utf-8")

    no_force = "ALTER TABLE communication_logs NO FORCE ROW LEVEL SECURITY"
    duplicate_gate = "HAVING count(*) > 1"
    force = "ALTER TABLE communication_logs FORCE ROW LEVEL SECURITY"
    unique_index = "uq_commlogs_zoom_phone_external_ref"
    assert source.index(no_force) < source.index(duplicate_gate) < source.index(force)
    assert source.index(force) < source.index(unique_index)
    assert "no customer rows were changed" in source


def test_zoom_account_binding_backfill_crosses_force_rls_then_restores_it():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "090_zoom_account_binding.py"
    ).read_text(encoding="utf-8")

    app_no_force = "ALTER TABLE tenant_oauth_apps NO FORCE ROW LEVEL SECURITY"
    credential_no_force = "ALTER TABLE tenant_credentials NO FORCE ROW LEVEL SECURITY"
    backfill = "UPDATE tenant_oauth_apps AS app"
    credential_force = "ALTER TABLE tenant_credentials FORCE ROW LEVEL SECURITY"
    app_force = "ALTER TABLE tenant_oauth_apps FORCE ROW LEVEL SECURITY"
    assert (
        source.index(app_no_force)
        < source.index(credential_no_force)
        < source.index(backfill)
        < source.index(credential_force)
        < source.index(app_force)
    )
    assert "app.tenant_id = credential.tenant_id" in source
    assert "app.provider = 'zoom_phone'" in source
    assert "credential.provider = 'zoom_phone'" in source


def test_online_migrations_seed_non_customer_rls_context():
    backend_dir = Path(__file__).resolve().parents[1]
    source = (backend_dir / "migrations" / "env.py").read_text(encoding="utf-8")
    online = source.split("def do_run_migrations", 1)[1].split(
        "async def run_async_migrations", 1
    )[0]

    sentinel = "00000000-0000-0000-0000-000000000000"
    migration_call = "context.run_migrations()"
    assert sentinel in online
    assert online.index("set_config('app.tenant_id'") < online.index(migration_call)
    assert online.index("set_config('app.current_tenant_id'") < online.index(
        migration_call
    )
    assert online.index("set_config('app.rls_bypass', 'off'") < online.index(
        migration_call
    )


def test_revision_ids_fit_alembic_version_column():
    """alembic_version.version_num is VARCHAR(32) — a longer revision id
    fails at apply time (StringDataRightTruncationError), not at import time,
    so this only ever surfaces during a real deploy unless checked here."""
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    script = ScriptDirectory.from_config(config)

    overlong = [
        rev.revision for rev in script.walk_revisions() if len(rev.revision) > 32
    ]
    assert overlong == []


def test_mcp_security_backfill_explicitly_enters_force_rls_policy():
    """The migration must also work for managed-Postgres owner roles."""
    backend_dir = Path(__file__).resolve().parents[1]
    source = (
        backend_dir / "migrations" / "versions" / "087_mcp_product_security.py"
    ).read_text(encoding="utf-8")

    bypass_on = "set_config('app.rls_bypass', 'on', true)"
    backfill = "UPDATE mcp_product_keys SET monthly_call_limit"
    bypass_off = "set_config('app.rls_bypass', 'off', true)"
    assert source.index(bypass_on) < source.index(backfill) < source.index(bypass_off)
