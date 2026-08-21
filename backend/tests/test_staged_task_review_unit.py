"""Pure invariants for staged staff-to-attorney task review."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.task import Task
from app.schemas.task import AttorneyOverrideRequest
from app.services.task_automation import (
    ActionApprovalConflict,
    enqueue_durable_automation,
)
from app.services.task_workflow import (
    TaskWorkflowError,
    record_review_decision,
    reset_staged_review_after_edit,
    staged_review_is_approved,
)


class _DB:
    def __init__(self):
        self.events = []

    def add(self, value):
        self.events.append(value)


def _task():
    staff = uuid4()
    attorney = uuid4()
    return (
        SimpleNamespace(
            id=uuid4(),
            tenant_id=uuid4(),
            status="review",
            version=1,
            review_policy="staff_then_attorney",
            review_stage="staff",
            reviewer_user_id=staff,
            staff_reviewer_user_id=staff,
            attorney_reviewer_user_id=attorney,
            staff_reviewed_at=None,
            staff_reviewed_by_user_id=None,
            attorney_approved_at=None,
            attorney_approved_by_user_id=None,
            attorney_override=False,
            pending_action={"type": "matter_document_draft"},
        ),
        staff,
        attorney,
    )


@pytest.mark.asyncio
async def test_staff_approval_advances_stage_without_approval_side_effect():
    task, staff, attorney = _task()
    db = _DB()
    actor = SimpleNamespace(id=staff, role="paralegal")

    event = await record_review_decision(
        db, task, actor=actor, stage="staff", decision="approve"
    )

    assert event == "staff_review_approved"
    assert task.status == "review"
    assert task.review_stage == "attorney_pending"
    assert task.reviewer_user_id == attorney
    assert task.staff_reviewed_by_user_id == staff
    assert task.version == 2


@pytest.mark.asyncio
async def test_staff_cannot_approve_attorney_stage():
    task, staff, _ = _task()
    task.review_stage = "attorney_pending"
    with pytest.raises(TaskWorkflowError) as exc:
        await record_review_decision(
            _DB(),
            task,
            actor=SimpleNamespace(id=staff, role="paralegal"),
            stage="staff",
            decision="approve",
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_attorney_override_records_explicit_bypass(monkeypatch):
    task, _, attorney = _task()
    db = _DB()

    async def allow(_db, _user):
        return True

    monkeypatch.setattr("app.services.task_workflow._is_attorney_capable", allow)

    event = await record_review_decision(
        db,
        task,
        actor=SimpleNamespace(id=attorney, role="attorney"),
        stage="attorney",
        decision="approve",
        override=True,
        reason="Urgent filing; counsel reviewed directly.",
    )
    assert event == "attorney_override_approved"
    assert task.review_stage == "approved"
    assert task.attorney_override is True
    assert task.attorney_approved_by_user_id == attorney
    assert staged_review_is_approved(task) is True


@pytest.mark.asyncio
async def test_capable_unassigned_attorney_cannot_override_staff_review(monkeypatch):
    task, _, _ = _task()

    async def allow(_db, _user):
        return True

    monkeypatch.setattr("app.services.task_workflow._is_attorney_capable", allow)

    with pytest.raises(TaskWorkflowError) as exc:
        await record_review_decision(
            _DB(),
            task,
            actor=SimpleNamespace(id=uuid4(), role="attorney"),
            stage="attorney",
            decision="approve",
            override=True,
            reason="Attempted reassignment bypass.",
        )

    assert exc.value.status_code == 403
    assert task.review_stage == "staff"
    assert task.attorney_approved_by_user_id is None
    assert task.attorney_override is False


@pytest.mark.asyncio
async def test_attorney_role_without_approval_capability_is_denied(monkeypatch):
    task, staff, attorney = _task()
    task.review_stage = "attorney_pending"
    task.reviewer_user_id = attorney
    task.staff_reviewed_at = object()
    task.staff_reviewed_by_user_id = staff

    async def deny(_db, _user):
        return False

    monkeypatch.setattr("app.services.task_workflow._is_attorney_capable", deny)
    with pytest.raises(TaskWorkflowError) as exc:
        await record_review_decision(
            _DB(),
            task,
            actor=SimpleNamespace(id=attorney, role="attorney"),
            stage="attorney",
            decision="approve",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_capable_but_unassigned_attorney_cannot_use_normal_approval(monkeypatch):
    task, staff, attorney = _task()
    task.review_stage = "attorney_pending"
    task.reviewer_user_id = attorney
    task.staff_reviewed_at = object()
    task.staff_reviewed_by_user_id = staff

    async def allow(_db, _user):
        return True

    monkeypatch.setattr("app.services.task_workflow._is_attorney_capable", allow)
    with pytest.raises(TaskWorkflowError) as exc:
        await record_review_decision(
            _DB(),
            task,
            actor=SimpleNamespace(id=uuid4(), role="staff"),
            stage="attorney",
            decision="approve",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_normal_two_stage_evidence_is_required_for_approval(monkeypatch):
    task, staff, attorney = _task()
    db = _DB()
    await record_review_decision(
        db,
        task,
        actor=SimpleNamespace(id=staff),
        stage="staff",
        decision="approve",
    )

    async def allow(_db, _user):
        return True

    monkeypatch.setattr("app.services.task_workflow._is_attorney_capable", allow)
    await record_review_decision(
        db,
        task,
        actor=SimpleNamespace(id=attorney),
        stage="attorney",
        decision="approve",
    )
    assert staged_review_is_approved(task) is True

    task.staff_reviewed_by_user_id = uuid4()
    assert staged_review_is_approved(task) is False


def test_edit_reset_clears_every_approval_binding():
    task, staff, attorney = _task()
    task.review_stage = "approved"
    task.reviewer_user_id = attorney
    task.staff_reviewed_at = object()
    task.staff_reviewed_by_user_id = staff
    task.attorney_approved_at = object()
    task.attorney_approved_by_user_id = attorney

    assert reset_staged_review_after_edit(task) is True
    assert task.review_stage == "staff"
    assert task.reviewer_user_id == staff
    assert task.staff_reviewed_at is None
    assert task.staff_reviewed_by_user_id is None
    assert task.attorney_approved_at is None
    assert task.attorney_approved_by_user_id is None
    assert task.attorney_override is False


def test_override_reason_rejects_whitespace():
    with pytest.raises(ValidationError):
        AttorneyOverrideRequest(expected_version=1, reason="   ")


def test_task_model_enforces_reviewer_and_evidence_identity():
    names = {constraint.name for constraint in Task.__table__.constraints}
    assert {
        "ck_tasks_staged_reviewers_distinct",
        "ck_tasks_review_evidence_pairs",
        "ck_tasks_staff_reviewer_evidence_actor",
        "ck_tasks_attorney_reviewer_evidence_actor",
        "ck_tasks_staff_stage_reviewer",
        "ck_tasks_attorney_stage_reviewer",
        "ck_tasks_approved_staff_evidence",
    } <= names


@pytest.mark.asyncio
async def test_outbound_enqueue_requires_live_approval_capability(monkeypatch):
    task, _, actor_id = _task()
    task.review_policy = "single"
    task.review_stage = "attorney"
    task.status = "in_progress"
    task.pending_action = {"type": "email_client"}

    async def deny(_db, _actor_user_id):
        return False

    monkeypatch.setattr(
        "app.services.task_automation._actor_can_approve_legal_work", deny
    )
    with pytest.raises(ActionApprovalConflict, match="Legal approval authority"):
        await enqueue_durable_automation(
            _DB(),
            task,
            from_status="review",
            to_status="in_progress",
            actor_user_id=actor_id,
        )
