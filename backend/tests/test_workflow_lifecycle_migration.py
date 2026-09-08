"""Frozen migration packaging and application event-contract agreement."""

import importlib.util
from pathlib import Path

from app.models.workflow_automation import TRIGGER_EVENTS


def test_lifecycle_migration_is_self_contained_and_keeps_function_bodies():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/166_workflow_lifecycle_events.py"
    )
    spec = importlib.util.spec_from_file_location("lifecycle_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = list(migration._statements(migration.CAPTURE_SQL))
    assert len(statements) == 3
    assert all(
        statement.startswith("CREATE FUNCTION") and statement.endswith("END $$")
        for statement in statements
    )
    assert all(
        "SET search_path=pg_catalog,public,pg_temp" in statement
        for statement in statements
    )
    assert "SECURITY DEFINER" not in "\n".join(statements)
    assert migration.EVENTS == TRIGGER_EVENTS
    assert list(
        migration._statements("-- Comment; discarded\nSELECT 1; SELECT 2;")
    ) == ["SELECT 1", "SELECT 2"]
