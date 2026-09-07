"""Demo purge policy for provider migration and alias state."""

import app.models  # noqa: F401
from app.database import Base
from app.services.demo_purge import _delete_order, _purge_tables
from app.services.demo_registry import DEMO_TABLE_REGISTRY


def test_migration_and_alias_tables_are_registered_for_purge():
    expected = {
        "storage_migrations",
        "storage_migration_matches",
        "onboarding_root_audits",
        "user_alias_addresses",
    }
    assert expected <= set(DEMO_TABLE_REGISTRY)
    assert expected <= set(_purge_tables())


def test_purge_dependency_order_places_children_before_migration_parents():
    tables = {name: Base.metadata.tables[name] for name in DEMO_TABLE_REGISTRY}
    order = _delete_order(tables)
    assert order.index("storage_migration_matches") < order.index("storage_migrations")
    assert order.index("user_alias_addresses") < order.index("users")
