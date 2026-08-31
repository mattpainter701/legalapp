import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.middleware.demo_quota import _surface
from app.models.tenant import Tenant
from app.services.demo_access import reject_demo_premium
from app.services.demo_clone import _remap_embedded, _required_dependency_order
from app.services.demo_purge import _delete_order, _purge_tables


def test_embedded_fixture_identifiers_are_recursively_remapped():
    old_id, new_id = uuid.uuid4(), uuid.uuid4()
    payload = {
        "matter_id": str(old_id),
        "nested": [old_id, {"source": str(old_id)}],
        "external_ref": f"intake-dashboard:call:{old_id}:general-task",
        "ordinary": "synthetic text",
    }

    remapped = _remap_embedded(payload, {old_id: new_id})

    assert remapped == {
        "matter_id": str(new_id),
        "nested": [new_id, {"source": str(new_id)}],
        "external_ref": f"intake-dashboard:call:{new_id}:general-task",
        "ordinary": "synthetic text",
    }


def test_clone_and_purge_orders_cover_the_registry_without_cycles():
    import app.main  # noqa: F401 -- load every router-owned model
    from app.database import Base
    from app.services.demo_registry import DEMO_TABLE_REGISTRY

    clone_tables = {
        name: Base.metadata.tables[name]
        for name, policy in DEMO_TABLE_REGISTRY.items()
        if policy.clone
    }
    purge_tables = {name: Base.metadata.tables[name] for name in DEMO_TABLE_REGISTRY}

    assert set(_required_dependency_order(clone_tables)) == set(clone_tables)
    assert set(_delete_order(purge_tables)) == set(purge_tables)


def test_runtime_purge_plan_registers_every_demo_table():
    """The scheduler must not depend on routers to populate Base.metadata."""
    from app.services.demo_registry import DEMO_TABLE_REGISTRY

    assert set(_purge_tables()) == set(DEMO_TABLE_REGISTRY)


def test_runtime_purge_plan_is_complete_in_a_fresh_process():
    """The purge worker and DB rehearsal must not depend on router import order."""
    backend_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.services.demo_purge import _purge_tables; "
                "from app.services.demo_registry import DEMO_TABLE_REGISTRY; "
                "assert set(_purge_tables()) == set(DEMO_TABLE_REGISTRY)"
            ),
        ],
        cwd=backend_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    ("path", "surface"),
    [
        ("/api/conversations/abc/messages", "chat"),
        ("/api/conversations/abc/messages/stream", "chat"),
        ("/api/office/plans", "office"),
        ("/api/plugins/litigation-legal/matter-intake", "plugin"),
        ("/api/plugins/litigation/matters", None),
        ("/api/conversations", None),
    ],
)
def test_demo_quota_only_classifies_model_spending_routes(path, surface):
    assert _surface(path, "POST") == surface


def test_demo_premium_requests_are_explicitly_rejected():
    user = type("DemoUser", (), {"tenant": Tenant(billing_tier="demo")})()
    with pytest.raises(HTTPException) as exc_info:
        reject_demo_premium(user, True)
    assert exc_info.value.status_code == 403


def test_non_demo_and_standard_requests_keep_existing_behavior():
    standard_demo_user = type("DemoUser", (), {"tenant": Tenant(billing_tier="demo")})()
    regular_user = type("User", (), {"tenant": Tenant(billing_tier="payg")})()

    reject_demo_premium(standard_demo_user, False)
    reject_demo_premium(regular_user, True)
