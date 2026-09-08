from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import workflow_runs as api
from app.services.automation_capabilities import (
    CapabilityError,
    resolve_capability_spec,
)
from app.services import workspace_mcp_budgets as budgets
from app.services import workspace_mcp_protocol as protocol


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,service_name,body",
    [
        ("create_run", "submit_run", None),
        ("continue_run", "resume_run", api.ResumeRunInput(expected_version=1)),
        ("stop_run", "cancel_run", api.VersionRequest(expected_version=1)),
    ],
)
async def test_web_adapters_preserve_not_found_and_version_failures(
    monkeypatch, method, service_name, body
):
    monkeypatch.setattr(api, "set_tenant_context", AsyncMock())
    user = NS(id=uuid4(), tenant_id=uuid4())
    db = NS(commit=AsyncMock())
    service = AsyncMock(
        side_effect=CapabilityError("run_not_found", "Workflow run not found")
    )
    monkeypatch.setattr(api, service_name, service)
    args = (body, db, user) if method == "create_run" else (uuid4(), body, db, user)
    with pytest.raises(HTTPException) as error:
        await getattr(api, method)(*args)
    assert error.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_web_adapter_preserves_binding_conflict(monkeypatch):
    from app.services import workflow_run_reconciliation as service

    monkeypatch.setattr(api, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        service,
        "reconcile_run",
        AsyncMock(
            side_effect=CapabilityError(
                "cloud_identity_conflict", "Different provider object"
            )
        ),
    )
    with pytest.raises(HTTPException) as error:
        await api.reconcile_cloud(
            uuid4(),
            api.CloudReconciliationRequest(
                expected_version=1, provider_object_id="file", reason="Located"
            ),
            NS(),
            NS(id=uuid4(), tenant_id=uuid4()),
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, 429, 503])
async def test_runtime_uses_same_live_grant_budget(monkeypatch, status):
    from redis.asyncio import Redis

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(Redis, "from_url", lambda *_: Connection())
    admission = AsyncMock(
        side_effect=HTTPException(status, "limited") if status else None
    )
    monkeypatch.setattr(budgets, "enforce_workspace_grant_call_budget", admission)
    run = NS(origin_channel="workspace_mcp", tenant_id=uuid4(), grant_id=uuid4())
    if status:
        with pytest.raises(CapabilityError) as error:
            await budgets.enforce_workspace_runtime_budget(run)
        assert error.value.code == (
            "workflow_rate_limited" if status == 429 else "workflow_budget_unavailable"
        )
    else:
        await budgets.enforce_workspace_runtime_budget(run)
    assert admission.call_args.args[1] is run
    run.origin_channel = "matter_chat"
    admission.reset_mock()
    await budgets.enforce_workspace_runtime_budget(run)
    admission.assert_not_awaited()


def test_workspace_run_audit_contains_identity_without_work_product():
    run_id = uuid4()
    result = {
        "run_id": str(run_id),
        "plan_sha256": "a" * 64,
        "status": "awaiting_review",
        "body": "private",
    }
    metadata = protocol._success_audit_metadata(
        resolve_capability_spec("propose_workflow_run"), result
    )
    assert metadata == {
        "effect": "propose",
        "run_id": str(run_id),
        "plan_sha256": "a" * 64,
        "run_status": "awaiting_review",
    }
