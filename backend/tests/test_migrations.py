from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_graph_resolves_heads():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()

    assert heads == ["088_scheduler_logs_rls"]


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
