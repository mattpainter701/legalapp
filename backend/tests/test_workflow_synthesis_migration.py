"""Frozen proposal migration retains database-level evidence and tenant guards."""

import importlib.util
from pathlib import Path


def test_proposal_migration_is_frozen_and_protects_exact_evidence():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/167_workflow_configuration_synthesis.py"
    )
    spec = importlib.util.spec_from_file_location("synthesis_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "167_workflow_synthesis"
    assert module.down_revision == "166_workflow_lifecycle_events"
    sql = "\n".join(module.DDL)
    assert "FOREIGN KEY(tenant_id, source_job_id)" in sql
    assert "UNIQUE (tenant_id, pattern_key, proposal_sha256)" in sql
    assert "rejection_reason IS NOT NULL" in sql
    assert "from app" not in path.read_text()
    functions = [part for part in module.GUARDS_SQL.split("END $$;") if part.strip()]
    assert len(functions) == 2
    assert all(
        "SET search_path=pg_catalog,public,pg_temp" in function
        for function in functions
    )
    assert "SECURITY DEFINER" not in module.GUARDS_SQL
    assert "j.status='running'" in module.GUARDS_SQL
    assert "Declined proposal cannot be approved" in module.GUARDS_SQL
