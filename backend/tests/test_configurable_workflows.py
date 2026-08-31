"""Unit coverage for deterministic COMP-09 workflow execution seams."""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.configurable_workflow import (
    CustomFieldDefinition,
    MatterWorkflowRun,
    MatterWorkflowRunStep,
)
from app.routers.configurable_workflows import (
    _field_response,
    router,
    update_field_definition,
)
from app.schemas.configurable_workflow import CustomFieldDefinitionUpdate
from app.services import configurable_workflows as workflows


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _SequenceDB:
    def __init__(self, sequence=1, rows=None):
        self.sequence = sequence
        self.rows = rows or []
        self.execute_count = 0
        self.added = []

    async def scalar(self, query):
        return self.sequence

    async def execute(self, query, params=None):
        rows = (
            self.rows[self.execute_count]
            if self.rows and isinstance(self.rows[0], list)
            else self.rows
        )
        self.execute_count += 1
        return _ScalarResult(rows)

    def add(self, value):
        self.added.append(value)


def _run(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "matter_id": uuid.uuid4(),
        "template_version_id": uuid.uuid4(),
        "idempotency_key": "preview-1",
        "request_sha256": "a" * 64,
        "template_sha256": "b" * 64,
        "matter_sha256": "c" * 64,
        "preview_sha256": "d" * 64,
        "preview_json": {
            "as_of": "2026-08-30",
            "initial_stage": {"stage_key": "intake", "label": "Intake"},
            "tasks": [],
        },
        "prior_stage": "New",
        "planned_by_user_id": uuid.uuid4(),
        "status": "planned",
    }
    values.update(overrides)
    return MatterWorkflowRun(**values)


@pytest.mark.asyncio
async def test_apply_rejects_missing_required_fields_and_stale_preview(monkeypatch):
    run = _run()
    matter = SimpleNamespace(id=run.matter_id, tenant_id=run.tenant_id)

    async def missing(*args, **kwargs):
        return ({"can_apply": False}, run.preview_sha256, "b" * 64, "c" * 64)

    monkeypatch.setattr(workflows, "build_preview", missing)
    with pytest.raises(HTTPException) as error:
        await workflows.apply_run(
            _SequenceDB(),
            run=run,
            matter=matter,
            actor_user_id=uuid.uuid4(),
            preview_sha256=run.preview_sha256,
        )
    assert error.value.status_code == 409
    assert "unresolved required fields or assignees" in error.value.detail

    with pytest.raises(HTTPException) as error:
        await workflows.apply_run(
            _SequenceDB(),
            run=run,
            matter=matter,
            actor_user_id=uuid.uuid4(),
            preview_sha256="e" * 64,
        )
    assert error.value.status_code == 409
    assert "Preview evidence" in error.value.detail


@pytest.mark.asyncio
async def test_applied_run_is_idempotent_but_different_preview_is_conflict():
    run = _run(status="applied")
    matter = SimpleNamespace(id=run.matter_id, tenant_id=run.tenant_id)
    assert (
        await workflows.apply_run(
            _SequenceDB(),
            run=run,
            matter=matter,
            actor_user_id=uuid.uuid4(),
            preview_sha256=run.preview_sha256,
        )
        is run
    )
    with pytest.raises(HTTPException) as error:
        await workflows.apply_run(
            _SequenceDB(),
            run=run,
            matter=matter,
            actor_user_id=uuid.uuid4(),
            preview_sha256="e" * 64,
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_apply_sets_transactional_target_state_before_evidence_flush(monkeypatch):
    run = _run()
    matter = SimpleNamespace(id=run.matter_id, tenant_id=run.tenant_id, stage="New")
    observed_statuses = []

    async def current_preview(*args, **kwargs):
        return (
            {
                "as_of": "2026-08-30",
                "initial_stage": {"stage_key": "intake", "label": "Intake"},
                "tasks": [],
                "can_apply": True,
            },
            run.preview_sha256,
            run.template_sha256,
            run.matter_sha256,
        )

    async def record_event(db, current_run, **kwargs):
        observed_statuses.append((kwargs["event_type"], current_run.status))

    async def record_step(*args, **kwargs):
        return None

    monkeypatch.setattr(workflows, "build_preview", current_preview)
    monkeypatch.setattr(workflows, "append_run_event", record_event)
    monkeypatch.setattr(workflows, "append_run_step", record_step)
    result = await workflows.apply_run(
        _SequenceDB(),
        run=run,
        matter=matter,
        actor_user_id=uuid.uuid4(),
        preview_sha256=run.preview_sha256,
    )
    assert result.status == "applied"
    assert observed_statuses == [("approved", "applied"), ("applied", "applied")]


@pytest.mark.asyncio
async def test_run_event_and_step_evidence_hashes_are_deterministic():
    run = _run()
    actor = uuid.uuid4()
    first_db = _SequenceDB(sequence=1)
    second_db = _SequenceDB(sequence=1)
    first_event = await workflows.append_run_event(
        first_db,
        run,
        event_type="previewed",
        actor_user_id=actor,
        detail={"can_apply": True},
    )
    second_event = await workflows.append_run_event(
        second_db,
        run,
        event_type="previewed",
        actor_user_id=actor,
        detail={"can_apply": True},
    )
    assert first_event.evidence_sha256 == second_event.evidence_sha256
    first_step = await workflows.append_run_step(
        first_db,
        run,
        step_type="matter_stage",
        action_key="intake",
        status="succeeded",
        evidence={"before": "New", "after": "Intake"},
    )
    second_step = await workflows.append_run_step(
        second_db,
        run,
        step_type="matter_stage",
        action_key="intake",
        status="succeeded",
        evidence={"before": "New", "after": "Intake"},
    )
    assert first_step.evidence_sha256 == second_step.evidence_sha256
    assert first_event.evidence_sha256 != first_step.evidence_sha256


def test_hashing_is_order_independent_and_preview_never_contains_raw_value():
    assert workflows.digest_payload({"b": 2, "a": 1}) == workflows.digest_payload(
        {"a": 1, "b": 2}
    )
    preview = {
        "missing_required_fields": [{"field_key": "ssn", "sensitive": True}],
        "matter_sha256": "x" * 64,
    }
    assert "123-45-6789" not in workflows.canonical_json(preview)


def test_sensitive_field_response_redacts_value_but_reports_presence():
    field = CustomFieldDefinition(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        entity_type="matter",
        field_key="ssn",
        label="SSN",
        field_type="text",
        options_json=[],
        sensitive=True,
        created_by_user_id=uuid.uuid4(),
    )
    response = _field_response(field, "123-45-6789")
    assert response["value"] is None
    assert response["has_value"] is True


@pytest.mark.asyncio
async def test_sensitive_field_classification_cannot_be_downgraded():
    field = CustomFieldDefinition(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        entity_type="matter",
        field_key="ssn",
        label="SSN",
        field_type="text",
        options_json=[],
        sensitive=True,
        schema_version=3,
        created_by_user_id=uuid.uuid4(),
    )

    class FieldDB(_SequenceDB):
        async def scalar(self, query):
            return field

    with pytest.raises(HTTPException) as error:
        await update_field_definition(
            field.id,
            CustomFieldDefinitionUpdate(expected_schema_version=3, sensitive=False),
            FieldDB(),
            SimpleNamespace(tenant_id=field.tenant_id),
        )
    assert error.value.status_code == 409
    assert "cannot be removed" in error.value.detail


@pytest.mark.asyncio
async def test_rollback_cancels_only_unchanged_pending_tasks_and_restores_stage(
    monkeypatch,
):
    run = _run(status="applied")
    actor = uuid.uuid4()
    task = SimpleNamespace(
        id=uuid.uuid4(),
        status="pending",
        version=1,
        title="Review",
        description=None,
        task_type="review",
        priority="medium",
        due_date=date(2026, 8, 30),
        assigned_to_user_id=None,
        external_ref=f"workflow:{run.id}:review",
    )
    step = MatterWorkflowRunStep(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=1,
        step_type="task_create",
        action_key="review",
        status="succeeded",
        task_id=task.id,
        evidence_json={
            "initial_version": 1,
            "title": "Review",
            "description": None,
            "task_type": "review",
            "priority": "medium",
            "due_date": "2026-08-30",
            "assigned_to_user_id": None,
            "external_ref": task.external_ref,
        },
    )
    db = _SequenceDB(rows=[[step], [task]])
    added = []

    async def record_event(*args, **kwargs):
        added.append(("event", kwargs["event_type"]))

    async def record_step(*args, **kwargs):
        added.append(("step", kwargs["step_type"]))

    monkeypatch.setattr(workflows, "append_run_event", record_event)
    monkeypatch.setattr(workflows, "append_run_step", record_step)
    monkeypatch.setattr(
        workflows,
        "transition_task",
        lambda db, item, **kwargs: setattr(item, "status", "cancelled"),
    )
    matter = SimpleNamespace(stage="Intake")
    result, blockers = await workflows.rollback_run(
        db,
        run=run,
        matter=matter,
        actor_user_id=actor,
        idempotency_key="rollback-1",
        request_sha256="f" * 64,
        reason="undo",
    )
    assert blockers == []
    assert result.status == "rolled_back"
    assert task.status == "cancelled"
    assert matter.stage == "New"


@pytest.mark.asyncio
async def test_rollback_reports_compensation_required_for_changed_task_or_stage(
    monkeypatch,
):
    run = _run(status="applied")
    task = SimpleNamespace(
        id=uuid.uuid4(),
        status="completed",
        version=2,
        title="Review",
        description=None,
        task_type="review",
        priority="medium",
        due_date=date(2026, 8, 30),
        assigned_to_user_id=None,
        external_ref="wrong",
    )
    step = MatterWorkflowRunStep(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=1,
        step_type="task_create",
        action_key="review",
        status="succeeded",
        task_id=task.id,
        evidence_json={
            "initial_version": 1,
            "title": "Review",
            "description": None,
            "task_type": "review",
            "priority": "medium",
            "due_date": "2026-08-30",
            "assigned_to_user_id": None,
            "external_ref": "expected",
        },
    )
    db = _SequenceDB(rows=[[step], [task]])

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(workflows, "append_run_event", noop)
    monkeypatch.setattr(workflows, "append_run_step", noop)
    result, blockers = await workflows.rollback_run(
        db,
        run=run,
        matter=SimpleNamespace(stage="Changed"),
        actor_user_id=uuid.uuid4(),
        idempotency_key="rollback-2",
        request_sha256="f" * 64,
        reason="undo",
    )
    assert result.status == "compensation_required"
    assert len(blockers) == 2
    assert "changed after apply" in blockers[0]
    assert "stage changed" in blockers[1]


@pytest.mark.asyncio
async def test_compensation_required_rollback_same_key_replays_without_events():
    run = _run(
        status="compensation_required",
        rollback_idempotency_key="rollback-1",
        rollback_request_sha256="f" * 64,
        failure_detail="task changed after apply",
    )

    class ReplayDB(_SequenceDB):
        async def scalar(self, query):
            return {"blockers": ["task changed after apply", "stage changed"]}

    db = ReplayDB()
    result, blockers = await workflows.rollback_run(
        db,
        run=run,
        matter=SimpleNamespace(stage="Changed"),
        actor_user_id=uuid.uuid4(),
        idempotency_key="rollback-1",
        request_sha256="f" * 64,
        reason="undo",
    )
    assert result is run
    assert blockers == ["task changed after apply", "stage changed"]
    assert db.execute_count == 0


@pytest.mark.asyncio
async def test_compensation_required_rollback_different_key_is_rejected():
    run = _run(
        status="compensation_required", failure_detail="task changed after apply"
    )
    with pytest.raises(HTTPException) as error:
        await workflows.rollback_run(
            _SequenceDB(),
            run=run,
            matter=SimpleNamespace(stage="Changed"),
            actor_user_id=uuid.uuid4(),
            idempotency_key="new-key-2",
            request_sha256="f" * 64,
            reason="undo",
        )
    assert error.value.status_code == 409
    assert "manual compensation" in error.value.detail


def test_workflow_routes_declare_required_capabilities():
    expected = {
        "create_workflow_template": "manage_workflows",
        "create_workflow_template_version": "manage_workflows",
        "approve_workflow_template_version": "approve_legal_work",
        "preview_matter_workflow": "manage_matters",
        "apply_matter_workflow": "approve_legal_work",
        "rollback_matter_workflow": "approve_legal_work",
    }
    by_name = {route.endpoint.__name__: route for route in router.routes}
    for name, capability in expected.items():
        dependency = by_name[name].dependant.dependencies[-1].call
        assert capability in {
            cell.cell_contents for cell in (dependency.__closure__ or ())
        }
