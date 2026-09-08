"""The deployable migration remains frozen and enforces the evidence boundary."""

import importlib.util
from pathlib import Path


def test_runtime_migration_retains_bounded_tenant_evidence():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/168_workflow_runtime.py"
    )
    spec = importlib.util.spec_from_file_location("runtime_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "168_workflow_runtime"
    assert module.down_revision == "167_workflow_synthesis"
    sql = "\n".join(module.DDL)
    assert "UNIQUE (tenant_id, actor_user_id, request_id)" in sql
    assert (
        "FOREIGN KEY(tenant_id, artifact_id, artifact_revision_id, approval_id)" in sql
    )
    assert "FOREIGN KEY(tenant_id, run_id)" in sql
    assert "position<12" in sql
    assert "from app" not in path.read_text()
    assert "SECURITY DEFINER" not in module.GUARDS_SQL
    assert (
        "Completed capability result and review binding are immutable"
        in module.GUARDS_SQL
    )
    assert (
        "Completed delivery steps require confirmed delivery evidence"
        in module.GUARDS_SQL
    )
    assert "A completed run requires every planned checkpoint" in module.GUARDS_SQL
