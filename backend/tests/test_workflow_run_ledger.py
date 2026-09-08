"""Tests for workflow run submission, retrieval, and continuation."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from app.services.automation_capabilities import CapabilityError
from app.services.workflow_run_contract import RunStepInput, WorkflowRunInput


@pytest.mark.asyncio
async def test_submit_run_automation_service_requires_rule_evidence():
    from app.services.workflow_run_ledger import submit_run

    matter_id = uuid4()
    body = WorkflowRunInput(
        request_id=uuid4(),
        matter_id=matter_id,
        objective="prepare_document",
        steps=[
            RunStepInput(
                step_key="task",
                capability="propose_task",
                arguments={"matter_id": str(matter_id), "title": "x"},
            )
        ],
    )
    db = NS(
        execute=AsyncMock(),
        scalar=AsyncMock(return_value=None),
        flush=AsyncMock(),
        add=Mock(),
    )
    for service_rule_id, service_rule_sha256 in ((None, "a" * 64), (uuid4(), None)):
        context = NS(
            channel="automation_service",
            service_rule_id=service_rule_id,
            service_rule_sha256=service_rule_sha256,
            tenant_id=uuid4(),
            actor_user_id=uuid4(),
            db=db,
        )
        with pytest.raises(CapabilityError) as error:
            await submit_run(context, body)
        assert error.value.code == "service_rule_unavailable"


@pytest.mark.asyncio
async def test_submit_run_automation_service_records_rule_sha256():
    from app.services import workflow_run_ledger as ledger

    rule_id = uuid4()
    rule_sha256 = "a" * 64
    tenant_id = uuid4()
    actor_user_id = uuid4()
    matter_id = uuid4()
    db = NS(
        execute=AsyncMock(),
        scalar=AsyncMock(return_value=None),
        flush=AsyncMock(),
        add=Mock(),
    )
    context = NS(
        channel="automation_service",
        service_rule_id=rule_id,
        service_rule_sha256=rule_sha256,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        grant_id=None,
        client_id=None,
        granted_scopes=None,
        allowed_sources=None,
        db=db,
    )
    body = WorkflowRunInput(
        request_id=uuid4(),
        matter_id=matter_id,
        objective="prepare_document",
        steps=[
            RunStepInput(
                step_key="task",
                capability="propose_task",
                arguments={"matter_id": str(matter_id), "title": "x"},
            )
        ],
    )

    async def capture_describe_run(db, run):
        return {"run_id": str(run.id), "plan_metadata": run.plan_json}

    with (
        patch.object(ledger, "digest_payload", return_value="h" * 64),
        patch.object(ledger, "plan_metadata", return_value={}),
        patch.object(ledger, "current_context", new_callable=AsyncMock),
        patch.object(ledger, "verify_source_bindings", new_callable=AsyncMock),
        patch.object(ledger, "append_event", new_callable=AsyncMock),
        patch.object(ledger, "resolve_capability_spec", return_value=NS(name="propose_task")),
        patch.object(ledger, "seal_payload", return_value=("cipher", "sha")),
        patch.object(ledger, "enqueue_job", new_callable=AsyncMock, return_value="job"),
        patch.object(ledger, "describe_run", new_callable=AsyncMock, side_effect=capture_describe_run),
    ):
        result = await ledger.submit_run(context, body)

    assert result["plan_metadata"]["service_rule_sha256"] == rule_sha256
