from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.routers.tasks as routes
from app.schemas.task import AttorneyOverrideRequest, TaskReviewDecisionRequest


class _Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, task) -> None:
        self.task = task
        self.commits = 0
        self.rollbacks = 0
        self.refreshes = []
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.task)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, value) -> None:
        self.refreshes.append(value)


def _user():
    return SimpleNamespace(id=uuid4(), tenant_id=uuid4(), role="attorney")


def _task(**overrides):
    values = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "matter_id": uuid4(),
        "version": 4,
        "status": "review",
        "review_policy": "staff_then_attorney",
        "review_stage": "staff",
        "staff_reviewer_user_id": uuid4(),
        "attorney_reviewer_user_id": uuid4(),
        "pending_action": {"type": "matter_document_draft"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_common(monkeypatch):
    tenant_calls = []

    async def tenant_context(db, tenant_id):
        tenant_calls.append((db, tenant_id))

    async def response(db, task):
        return {"task_id": str(task.id), "status": task.status, "version": task.version}

    monkeypatch.setattr(routes, "set_tenant_context", tenant_context)
    monkeypatch.setattr(routes, "_task_response_with_delivery", response)
    return tenant_calls


@pytest.mark.asyncio
async def test_staff_review_records_and_returns_locked_task(monkeypatch):
    user = _user()
    task = _task(tenant_id=user.tenant_id, staff_reviewer_user_id=user.id)
    db = _DB(task)
    tenant_calls = _install_common(monkeypatch)
    decisions = []

    async def record(db_arg, task_arg, **kwargs):
        decisions.append((db_arg, task_arg, kwargs))

    monkeypatch.setattr(routes, "record_review_decision", record)
    payload = TaskReviewDecisionRequest(
        decision="approve", expected_version=task.version, reason="Reviewed"
    )

    result = await routes.review_task_as_staff(
        task.id, payload, current_user=user, db=db
    )

    assert result == {
        "task_id": str(task.id),
        "status": "review",
        "version": task.version,
    }
    assert len(db.statements) == 1
    assert db.commits == 1
    assert db.refreshes == [task]
    assert tenant_calls == [
        (db, str(user.tenant_id)),
        (db, str(user.tenant_id)),
    ]
    assert decisions == [
        (
            db,
            task,
            {
                "actor": user,
                "stage": "staff",
                "decision": "approve",
                "reason": "Reviewed",
            },
        )
    ]


@pytest.mark.asyncio
async def test_staff_review_rejects_missing_or_stale_task(monkeypatch):
    user = _user()
    _install_common(monkeypatch)
    payload = TaskReviewDecisionRequest(decision="approve", expected_version=4)

    with pytest.raises(HTTPException) as missing:
        await routes.review_task_as_staff(
            uuid4(), payload, current_user=user, db=_DB(None)
        )
    assert missing.value.status_code == 404

    stale_task = _task(tenant_id=user.tenant_id, version=5)
    with pytest.raises(HTTPException) as stale:
        await routes.review_task_as_staff(
            stale_task.id, payload, current_user=user, db=_DB(stale_task)
        )
    assert stale.value.status_code == 409
    assert stale.value.detail == (
        "This task changed after the board was loaded. "
        "Review the latest task and try again."
    )


@pytest.mark.asyncio
async def test_attorney_approval_transitions_and_enqueues_automation(monkeypatch):
    user = _user()
    task = _task(tenant_id=user.tenant_id, attorney_reviewer_user_id=user.id)
    db = _DB(task)
    tenant_calls = _install_common(monkeypatch)
    decisions = []
    transitions = []
    automations = []

    async def record(db_arg, task_arg, **kwargs):
        decisions.append((db_arg, task_arg, kwargs))

    def transition(db_arg, task_arg, **kwargs):
        transitions.append((db_arg, task_arg, kwargs))
        task_arg.status = kwargs["to_status"]
        task_arg.version += 1

    async def enqueue(db_arg, task_arg, **kwargs):
        automations.append((db_arg, task_arg, kwargs))

    monkeypatch.setattr(routes, "record_review_decision", record)
    monkeypatch.setattr(routes, "transition_task", transition)
    monkeypatch.setattr(routes, "enqueue_durable_automation", enqueue)
    payload = TaskReviewDecisionRequest(
        decision="approve", expected_version=task.version, reason="Approved"
    )

    result = await routes.review_task_as_attorney(
        task.id, payload, current_user=user, db=db
    )

    assert result["status"] == "in_progress"
    assert db.commits == 1
    assert db.rollbacks == 0
    assert db.refreshes == [task]
    assert tenant_calls == [
        (db, str(user.tenant_id)),
        (db, str(user.tenant_id)),
    ]
    assert decisions[0][2] == {
        "actor": user,
        "stage": "attorney",
        "decision": "approve",
        "reason": "Approved",
        "override": False,
    }
    assert transitions[0][2]["to_status"] == "in_progress"
    assert transitions[0][2]["actor_user_id"] == user.id
    assert automations[0][2] == {
        "from_status": "review",
        "to_status": "in_progress",
        "actor_user_id": user.id,
    }


@pytest.mark.asyncio
async def test_attorney_request_changes_records_review_without_transition(monkeypatch):
    user = _user()
    task = _task(tenant_id=user.tenant_id, attorney_reviewer_user_id=user.id)
    db = _DB(task)
    _install_common(monkeypatch)
    decisions = []

    async def record(db_arg, task_arg, **kwargs):
        decisions.append((db_arg, task_arg, kwargs))

    monkeypatch.setattr(routes, "record_review_decision", record)
    payload = TaskReviewDecisionRequest(
        decision="request_changes",
        expected_version=task.version,
        reason="Correct the caption",
    )

    result = await routes.review_task_as_attorney(
        task.id, payload, current_user=user, db=db
    )

    assert result["status"] == "review"
    assert db.commits == 1
    assert db.refreshes == [task]
    assert decisions[0][2] == {
        "actor": user,
        "stage": "attorney",
        "decision": "request_changes",
        "reason": "Correct the caption",
    }


@pytest.mark.asyncio
async def test_attorney_review_wrappers_preserve_override_semantics(monkeypatch):
    user = _user()
    db = _DB(None)
    calls = []

    async def approve(task_id, payload, current_user, db_arg, *, override=False):
        calls.append((task_id, payload, current_user, db_arg, override))
        return {"override": override}

    monkeypatch.setattr(routes, "_approve_staged_task", approve)
    task_id = uuid4()
    ordinary = TaskReviewDecisionRequest(decision="approve", expected_version=2)
    override = AttorneyOverrideRequest(expected_version=2, reason="Urgent filing")

    ordinary_result = await routes.review_task_as_attorney(
        task_id, ordinary, current_user=user, db=db
    )
    override_result = await routes.override_staff_review(
        task_id, override, current_user=user, db=db
    )

    assert ordinary_result == {"override": False}
    assert override_result == {"override": True}
    assert calls == [
        (task_id, ordinary, user, db, False),
        (task_id, override, user, db, True),
    ]
