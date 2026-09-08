"""The unattended-service migration must retain noninteractive and tenant bounds."""

import importlib.util
from pathlib import Path


def test_service_migration_has_rls_and_database_guards():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/169_automation_services.py"
    spec = importlib.util.spec_from_file_location("service_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = path.read_text()
    assert module.revision == "169_automation_services"
    assert module.down_revision == "168_workflow_runtime"
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "Automation service principals cannot receive roles" in source
    assert "permanently noninteractive" in source
    assert "Service occurrence must bind its exact run and identity" in source
    assert "Workflow evidence exists" not in source

