import app.models  # noqa: F401 -- registers every model table with Base.metadata
from app.database import Base
from app.services.demo_registry import DEMO_TABLE_REGISTRY, SENSITIVE_NEVER_CLONE


def test_every_tenant_scoped_table_has_an_explicit_purge_policy():
    tenant_tables = {
        table.name for table in Base.metadata.tables.values() if "tenant_id" in table.c
    }

    assert set(DEMO_TABLE_REGISTRY) == tenant_tables
    assert all(policy.purge for policy in DEMO_TABLE_REGISTRY.values())


def test_clone_is_a_strict_subset_of_the_purge_registry():
    clone_tables = {
        policy.table for policy in DEMO_TABLE_REGISTRY.values() if policy.clone
    }
    purge_tables = {
        policy.table for policy in DEMO_TABLE_REGISTRY.values() if policy.purge
    }

    assert clone_tables < purge_tables
    assert "documents" in clone_tables
    assert "chunks" in clone_tables
    assert "usage_records" not in clone_tables
    assert "demo_sessions" not in clone_tables


def test_sensitive_integration_and_credential_tables_never_clone():
    assert SENSITIVE_NEVER_CLONE <= set(DEMO_TABLE_REGISTRY)
    assert all(not DEMO_TABLE_REGISTRY[table].clone for table in SENSITIVE_NEVER_CLONE)
