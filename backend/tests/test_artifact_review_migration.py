import importlib.util
from pathlib import Path


def test_frozen_guard_script_preserves_function_bodies_and_discards_comments():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/165_artifact_review_spine.py"
    )
    spec = importlib.util.spec_from_file_location("artifact_review_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = list(migration._statements(migration.REVIEW_GUARDS_SQL))
    assert len(statements) == 9
    assert all(not statement.startswith("--") for statement in statements)
    functions = [
        statement for statement in statements if statement.startswith("CREATE FUNCTION")
    ]
    assert len(functions) == 4
    assert all(statement.endswith("END $$") for statement in functions)
    assert all(
        "SET search_path = pg_catalog, public, pg_temp" in statement
        for statement in functions
    )
    assert list(
        migration._statements("-- Comment; must not become SQL\nSELECT 1;")
    ) == ["SELECT 1"]
