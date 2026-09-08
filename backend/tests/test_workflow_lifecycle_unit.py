"""Lifecycle planner boundaries, frozen evidence, and scheduler context."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.workflow_automation import TRIGGER_EVENTS
from app.schemas.workflow_automation import WorkflowAutomationRuleInput
from app.services import workflow_lifecycle as lifecycle


def fixture(monkeypatch):
    tenant, rule_id, matter_id, actor_id, source_id, version = [
        uuid4() for _ in range(6)
    ]
    payload = dict(
        rule_id=str(rule_id),
        matter_id=str(matter_id),
        actor_user_id=str(actor_id),
        source_id=str(source_id),
        trigger_event="task_completed",
        source_kind="task",
        rule_sha256="a" * 64,
        context_sha256="b" * 64,
        dedupe_key="c" * 64,
        template_version_id=str(version),
        as_of="2026-09-08",
    )
    rule = SimpleNamespace(
        id=rule_id,
        tenant_id=tenant,
        status="active",
        definition_sha256="a" * 64,
        activated_by_user_id=actor_id,
        template_id=uuid4(),
    )
    matter = SimpleNamespace(id=matter_id, tenant_id=tenant, archived_at=None)
    actor = SimpleNamespace(id=actor_id, is_active=True, license_active=True)
    event = SimpleNamespace(id=uuid4(), outcome="planned")
    db = AsyncMock()
    db.scalar.side_effect = [rule, matter, actor, "b" * 64]
    monkeypatch.setattr(lifecycle.planning, "acquire_workflow_config_lock", AsyncMock())
    monkeypatch.setattr(
        lifecycle.planning, "_existing_dispatch", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        lifecycle.planning,
        "latest_approved_version_id",
        AsyncMock(return_value=version),
    )
    monkeypatch.setattr(
        lifecycle.planning, "_plan_for_rule", AsyncMock(return_value=event)
    )
    monkeypatch.setattr(
        lifecycle.planning,
        "_record_dispatch",
        Mock(return_value=SimpleNamespace(id=uuid4(), outcome="blocked")),
    )
    monkeypatch.setattr(
        lifecycle, "get_user_capabilities", AsyncMock(return_value={"manage_matters"})
    )
    return db, SimpleNamespace(tenant_id=tenant, payload=payload), rule, matter, actor


@pytest.mark.asyncio
async def test_current_source_preserves_occurrence_and_frozen_evidence(monkeypatch):
    db, job, *_ = fixture(monkeypatch)
    assert (await lifecycle.run_lifecycle_job(db, job))["outcome"] == "planned"
    options = lifecycle.planning._plan_for_rule.await_args.kwargs
    assert options["dispatch_key"] == job.payload["dedupe_key"]
    assert options["as_of"] == date(2026, 9, 8)
    assert options["trigger_evidence"] == {
        key: job.payload[key] for key in ("source_id", "source_kind", "context_sha256")
    }
    for call in db.scalar.await_args_list[:3]:
        assert "tenant_id" in str(call.args[0])
    lifecycle.planning._record_dispatch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,reason",
    [
        ("inactive", "actor_unavailable"),
        ("unlicensed", "actor_unavailable"),
        ("permission", "actor_permission_changed"),
        ("archived_rule", "rule_changed"),
        ("edited_rule", "rule_changed"),
        ("new_approver", "rule_changed"),
        ("archived_matter", "matter_archived"),
        ("source", "source_changed"),
        ("template", "template_changed"),
        ("missing_template", "template_changed"),
    ],
)
async def test_context_changes_record_blocked_evidence(monkeypatch, change, reason):
    db, job, rule, matter, actor = fixture(monkeypatch)
    if change == "inactive":
        actor.is_active = False
    elif change == "unlicensed":
        actor.license_active = False
    elif change == "permission":
        lifecycle.get_user_capabilities.return_value = set()
    elif change == "archived_rule":
        rule.status = "archived"
    elif change == "edited_rule":
        rule.definition_sha256 = "d" * 64
    elif change == "new_approver":
        rule.activated_by_user_id = uuid4()
    elif change == "archived_matter":
        matter.archived_at = "archived"
    elif change == "source":
        db.scalar.side_effect = [rule, matter, actor, None]
    elif change == "template":
        lifecycle.planning.latest_approved_version_id.return_value = uuid4()
    elif change == "missing_template":
        lifecycle.planning.latest_approved_version_id.return_value = None
    assert (await lifecycle.run_lifecycle_job(db, job))["outcome"] == "blocked"
    detail = lifecycle.planning._record_dispatch.call_args.kwargs["detail"]
    assert detail["failure_code"] == reason
    assert detail["context_sha256"] == job.payload["context_sha256"]
    lifecycle.planning._plan_for_rule.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing,reason",
    [
        ("rule", "source_unavailable"),
        ("matter", "source_unavailable"),
        ("actor", "actor_unavailable"),
    ],
)
async def test_deleted_context_has_explicit_job_outcome(monkeypatch, missing, reason):
    db, job, rule, matter, actor = fixture(monkeypatch)
    db.scalar.side_effect = [
        None if missing == "rule" else rule,
        None if missing == "matter" else matter,
        None if missing == "actor" else actor,
    ]
    assert await lifecycle.run_lifecycle_job(db, job) == {
        "outcome": "blocked",
        "failure_code": reason,
    }
    lifecycle.planning._plan_for_rule.assert_not_called()


@pytest.mark.asyncio
async def test_replay_returns_original_dispatch_without_replanning(monkeypatch):
    db, job, *_ = fixture(monkeypatch)
    original = SimpleNamespace(id=uuid4(), outcome="blocked")
    lifecycle.planning._existing_dispatch.return_value = original
    assert await lifecycle.run_lifecycle_job(db, job) == {
        "outcome": "blocked",
        "event_id": str(original.id),
    }
    lifecycle.planning._plan_for_rule.assert_not_called()
    assert db.scalar.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("trigger_event", "execute"),
        ("source_kind", "users"),
        ("source_id", "not-a-uuid"),
    ],
)
async def test_invalid_payload_never_touches_database(monkeypatch, field, value):
    db, job, *_ = fixture(monkeypatch)
    job.payload[field] = value
    with pytest.raises(ValueError):
        await lifecycle.run_lifecycle_job(db, job)
    db.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_infrastructure_failure_is_left_for_worker_transaction_recovery(
    monkeypatch,
):
    db, job, *_ = fixture(monkeypatch)
    lifecycle.planning._plan_for_rule.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await lifecycle.run_lifecycle_job(db, job)
    db.commit.assert_not_called()


@pytest.mark.parametrize("event", TRIGGER_EVENTS)
def test_rule_schema_exposes_only_bounded_events_with_stage_only_for_stage_change(
    event,
):
    definition = dict(name="Rule", trigger_event=event, template_id=uuid4())
    if event == "matter_stage_changed":
        definition["trigger_stage"] = "Opening"
    assert WorkflowAutomationRuleInput(**definition).trigger_event == event
    if event != "matter_stage_changed":
        with pytest.raises(ValidationError):
            WorkflowAutomationRuleInput(**definition, trigger_stage="Opening")


@pytest.mark.asyncio
async def test_scheduler_preserves_tenant_and_commits_capture(monkeypatch):
    from app.services import scheduler

    db = AsyncMock()
    manager = Mock()
    manager.return_value.__aenter__ = AsyncMock(return_value=db)
    manager.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(scheduler, "async_session_maker", manager)
    monkeypatch.setattr(scheduler, "set_tenant_context", AsyncMock())
    capture = AsyncMock()
    monkeypatch.setattr(lifecycle, "enqueue_due_events_for_tenant", capture)
    tenant = uuid4()
    token = scheduler._scheduler_tenant_id.set(tenant)
    try:
        await scheduler.LegalScheduler.run_workflow_due_events(None)
    finally:
        scheduler._scheduler_tenant_id.reset(token)
    scheduler.set_tenant_context.assert_awaited_once_with(db, str(tenant))
    capture.assert_awaited_once_with(db, tenant)
    db.commit.assert_awaited_once()


def test_due_scan_is_registered_for_startup_and_recurring_execution():
    from app.services.scheduler import LegalScheduler

    instance = LegalScheduler()
    instance.scheduler = Mock()
    instance.start()
    jobs = [
        call
        for call in instance.scheduler.add_job.call_args_list
        if call.kwargs.get("id") == "workflow-due-events"
    ]
    assert len(jobs) == 1
    assert jobs[0].args[1] == "interval"
    assert jobs[0].kwargs["minutes"] == 15
    assert jobs[0].kwargs["max_instances"] == 1
    assert jobs[0].kwargs["next_run_time"] is not None


@pytest.mark.asyncio
async def test_lifecycle_events_cannot_use_the_legacy_matter_dedupe_path():
    from app.services.durable_workflow_automations import enqueue_matter_event

    db = AsyncMock()
    with pytest.raises(ValueError, match="Unsupported workflow trigger"):
        await enqueue_matter_event(
            db,
            matter=SimpleNamespace(),
            trigger_event="payment_received",
            actor_user_id=uuid4(),
        )
    db.flush.assert_not_called()
