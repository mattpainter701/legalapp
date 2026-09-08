"""Tests for tenant-sc scheduler admission of unattended service rules."""

from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.automation_capabilities import CapabilityError
from app.services.automation_service_contract import ServiceSchedule
from app.services.automation_service_scheduler import schedule_due_rules
from app.services.workflow_run_contract import RunStepInput, WorkflowRunInput


def _rule(schedule, **kwargs):
    defaults = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        identity_id=uuid4(),
        matter_id=uuid4(),
        approved_by_user_id=uuid4(),
        definition_sha256="d" * 64,
        payload_sha256="p" * 64,
        plan_ciphertext="cipher",
        approved_at=datetime.now(timezone.utc),
        event_rule_id=None,
        event_rule_sha256=None,
    )
    defaults["schedule"] = schedule.model_dump(mode="json")
    defaults.update(kwargs)
    return NS(**defaults)


def _scheduler_db(rules, scalar_results):
    return NS(
        scalars=AsyncMock(return_value=NS(all=lambda: rules)),
        scalar=AsyncMock(side_effect=scalar_results),
        add=MagicMock(),
        flush=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_schedule_due_rules_empty_tenant():
    db = _scheduler_db([], [])
    outcomes = await schedule_due_rules(db, uuid4())
    assert outcomes == []


@pytest.mark.asyncio
async def test_schedule_due_rules_skips_not_due():
    rule = _rule(ServiceSchedule(kind="daily", local_time="02:00"))
    db = _scheduler_db([rule], [])
    with patch(
        "app.services.automation_service_scheduler.due_occurrence",
        return_value=None,
    ):
        outcomes = await schedule_due_rules(db, rule.tenant_id, now=datetime.now(timezone.utc))
    assert outcomes == []


@pytest.mark.asyncio
async def test_schedule_due_rules_event_without_source_event():
    rule = _rule(
        ServiceSchedule(kind="workflow_event", event_rule_id=uuid4()),
    )
    db = _scheduler_db([rule], [None])
    outcomes = await schedule_due_rules(db, rule.tenant_id)
    assert outcomes == []


@pytest.mark.asyncio
async def test_schedule_due_rules_skips_inactive_identity():
    rule = _rule(ServiceSchedule(kind="daily", local_time="02:00"))
    db = _scheduler_db([rule], [None])
    with patch(
        "app.services.automation_service_scheduler.due_occurrence",
        return_value="date:2026-01-01",
    ):
        outcomes = await schedule_due_rules(db, rule.tenant_id)
    assert outcomes == []


@pytest.mark.asyncio
async def test_schedule_due_rules_returns_existing_occurrence():
    rule = _rule(ServiceSchedule(kind="daily", local_time="02:00"))
    existing = NS(id=uuid4())
    db = _scheduler_db(
        [rule],
        [
            NS(id=rule.identity_id, status="active"),
            existing,
        ],
    )
    with patch(
        "app.services.automation_service_scheduler.due_occurrence",
        return_value="date:2026-01-01",
    ):
        outcomes = await schedule_due_rules(db, rule.tenant_id)
    assert outcomes == [existing]


@pytest.mark.asyncio
async def test_schedule_due_rules_blocks_identity_budget():
    rule = _rule(ServiceSchedule(kind="daily", local_time="02:00"))
    db = _scheduler_db(
        [rule],
        [
            NS(id=rule.identity_id, status="active"),
            None,
            10,
            0,
        ],
    )
    with patch(
        "app.services.automation_service_scheduler.due_occurrence",
        return_value="date:2026-01-01",
    ):
        outcomes = await schedule_due_rules(db, rule.tenant_id)
    assert len(outcomes) == 1
    assert outcomes[0].outcome == "blocked"
    assert outcomes[0].failure_code == "identity_daily_budget"


@pytest.mark.asyncio
async def test_schedule_due_rules_blocks_tenant_budget():
    rule = _rule(ServiceSchedule(kind="daily", local_time="02:00"))
    db = _scheduler_db(
        [rule],
        [
            NS(id=rule.identity_id, status="active"),
            None,
            0,
            100,
        ],
    )
    with patch(
        "app.services.automation_service_scheduler.due_occurrence",
        return_value="date:2026-01-01",
    ):
        outcomes = await schedule_due_rules(db, rule.tenant_id)
    assert outcomes[0].outcome == "blocked"
    assert outcomes[0].failure_code == "tenant_daily_budget"


@pytest.mark.asyncio
async def test_schedule_due_rules_starts_run_from_sealed_plan():
    rule = _rule(ServiceSchedule(kind="daily", local_time="02:00"))
    service_user = NS(id=rule.identity_id, tenant_id=rule.tenant_id)
    plan = WorkflowRunInput(
        request_id=uuid4(),
        matter_id=rule.matter_id,
        objective="prepare_document",
        steps=[
            RunStepInput(
                step_key="task",
                capability="propose_task",
                arguments={"matter_id": str(rule.matter_id), "title": "x"},
            )
        ],
    )
    run_result = {"run_id": str(uuid4())}
    db = _scheduler_db(
        [rule],
        [
            NS(id=rule.identity_id, status="active", user_id=service_user.id),
            None,
            0,
            0,
            service_user,
        ],
    )
    with patch(
        "app.services.automation_service_scheduler.due_occurrence",
        return_value="date:2026-01-01",
    ), patch(
        "app.services.automation_service_scheduler.open_payload",
        return_value={"plan": plan.model_dump(mode="json"), "sources": None},
    ), patch(
        "app.services.automation_service_scheduler.submit_run",
        new_callable=AsyncMock,
        return_value=run_result,
    ):
        outcomes = await schedule_due_rules(db, rule.tenant_id)

    assert len(outcomes) == 1
    assert outcomes[0].outcome == "started"
    assert str(outcomes[0].run_id) == run_result["run_id"]
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_due_rules_blocks_when_submit_run_fails():
    rule = _rule(ServiceSchedule(kind="daily", local_time="02:00"))
    service_user = NS(id=rule.identity_id, tenant_id=rule.tenant_id)
    plan = WorkflowRunInput(
        request_id=uuid4(),
        matter_id=rule.matter_id,
        objective="prepare_document",
        steps=[
            RunStepInput(
                step_key="task",
                capability="propose_task",
                arguments={"matter_id": str(rule.matter_id), "title": "x"},
            )
        ],
    )
    db = _scheduler_db(
        [rule],
        [
            NS(id=rule.identity_id, status="active", user_id=service_user.id),
            None,
            0,
            0,
            service_user,
        ],
    )
    with patch(
        "app.services.automation_service_scheduler.due_occurrence",
        return_value="date:2026-01-01",
    ), patch(
        "app.services.automation_service_scheduler.open_payload",
        return_value={"plan": plan.model_dump(mode="json"), "sources": None},
    ), patch(
        "app.services.automation_service_scheduler.submit_run",
        new_callable=AsyncMock,
        side_effect=CapabilityError("service_identity_unavailable", "no"),
    ):
        outcomes = await schedule_due_rules(db, rule.tenant_id)

    assert outcomes[0].outcome == "blocked"
    assert outcomes[0].failure_code == "service_identity_unavailable"
