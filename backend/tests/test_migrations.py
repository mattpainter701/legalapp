from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_revision_graph_resolves_heads():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()

    assert heads == ["083_matter_doc_storage_meta"]


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
