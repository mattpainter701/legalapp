from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.task_workflow import (
    TaskVersionConflict,
    TaskWorkflowError,
    record_review_decision,
    require_review_actor,
    staged_review_is_approved,
)


class _DB:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


def _task(**overrides):
    staff_id = uuid4()
    attorney_id = uuid4()
    values = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "status": "review",
        "version": 1,
        "review_policy": "staff_then_attorney",
        "review_stage": "staff",
        "staff_reviewer_user_id": staff_id,
        "attorney_reviewer_user_id": attorney_id,
        "reviewer_user_id": staff_id,
        "staff_reviewed_at": None,
        "staff_reviewed_by_user_id": None,
        "attorney_approved_at": None,
        "attorney_approved_by_user_id": None,
        "attorney_override": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_workflow_errors_retain_http_safe_status_and_conflict_detail():
    error = TaskWorkflowError("invalid transition", status_code=403)
    conflict = TaskVersionConflict()

    assert error.detail == "invalid transition" and error.status_code == 403
    assert conflict.status_code == 409
    assert "changed after the board was loaded" in conflict.detail


def test_staged_approval_requires_complete_two_person_evidence():
    item = _task(review_stage="approved")
    assert staged_review_is_approved(item) is False

    now = datetime.now(timezone.utc)
    item.staff_reviewed_at = now
    item.staff_reviewed_by_user_id = item.staff_reviewer_user_id
    item.attorney_approved_at = now
    item.attorney_approved_by_user_id = item.attorney_reviewer_user_id
    assert staged_review_is_approved(item) is True

    item.attorney_override = True
    item.staff_reviewed_at = None
    item.staff_reviewed_by_user_id = None
    assert staged_review_is_approved(item) is True

    item.attorney_approved_by_user_id = item.staff_reviewer_user_id
    assert staged_review_is_approved(item) is False


@pytest.mark.asyncio
async def test_staff_review_authorization_fails_closed_and_accepts_only_assignee():
    db = _DB()
    actor = SimpleNamespace(id=uuid4())

    with pytest.raises(TaskWorkflowError, match="does not use staged review"):
        await require_review_actor(
            db, _task(review_policy="none"), actor, stage="staff"
        )
    with pytest.raises(TaskWorkflowError, match="not awaiting review"):
        await require_review_actor(db, _task(status="pending"), actor, stage="staff")
    with pytest.raises(TaskWorkflowError, match="not currently required"):
        await require_review_actor(
            db, _task(review_stage="attorney_pending"), actor, stage="staff"
        )
    with pytest.raises(TaskWorkflowError, match="assigned staff reviewer"):
        await require_review_actor(db, _task(), actor, stage="staff")

    item = _task()
    await require_review_actor(
        db,
        item,
        SimpleNamespace(id=item.staff_reviewer_user_id),
        stage="staff",
    )


@pytest.mark.asyncio
async def test_attorney_review_authorization_covers_capability_stage_and_override(
    monkeypatch,
):
    from app.services import rbac_service

    capabilities = set()

    async def live_capabilities(_db, _user_id):
        return capabilities

    monkeypatch.setattr(rbac_service, "get_user_capabilities", live_capabilities)
    item = _task(review_stage="attorney_pending")
    attorney = SimpleNamespace(id=item.attorney_reviewer_user_id)

    with pytest.raises(TaskWorkflowError, match="capability is required"):
        await require_review_actor(_DB(), item, attorney, stage="attorney")

    capabilities.add("approve_legal_work")
    with pytest.raises(TaskWorkflowError, match="assigned attorney reviewer"):
        await require_review_actor(
            _DB(), item, SimpleNamespace(id=uuid4()), stage="attorney"
        )

    item.review_stage = "approved"
    with pytest.raises(TaskWorkflowError, match="cannot be overridden"):
        await require_review_actor(
            _DB(), item, attorney, stage="attorney", override=True
        )

    item.review_stage = "staff"
    await require_review_actor(_DB(), item, attorney, stage="attorney", override=True)

    item.review_stage = "staff"
    with pytest.raises(TaskWorkflowError, match="not currently required"):
        await require_review_actor(_DB(), item, attorney, stage="attorney")

    item.review_stage = "attorney_pending"
    item.reviewer_user_id = uuid4()
    with pytest.raises(TaskWorkflowError, match="assigned attorney reviewer"):
        await require_review_actor(_DB(), item, attorney, stage="attorney")

    item.reviewer_user_id = attorney.id
    await require_review_actor(_DB(), item, attorney, stage="attorney")
    with pytest.raises(TaskWorkflowError, match="Unsupported review stage"):
        await require_review_actor(_DB(), item, attorney, stage="manager")


@pytest.mark.asyncio
async def test_review_decisions_cover_changes_override_and_invalid_decision(
    monkeypatch,
):
    from app.services import rbac_service

    async def attorney_capabilities(_db, _user_id):
        return {"approve_legal_work"}

    monkeypatch.setattr(rbac_service, "get_user_capabilities", attorney_capabilities)
    db = _DB()
    item = _task()
    staff = SimpleNamespace(id=item.staff_reviewer_user_id)

    with pytest.raises(TaskWorkflowError, match="Unsupported review decision"):
        await record_review_decision(
            db, item, actor=staff, stage="staff", decision="abstain"
        )

    event_type = await record_review_decision(
        db,
        item,
        actor=staff,
        stage="staff",
        decision="request_changes",
        reason="  Correct caption  ",
    )
    assert event_type == "staff_changes_requested"
    assert db.added[-1].note == "Correct caption"

    attorney = SimpleNamespace(id=item.attorney_reviewer_user_id)
    event_type = await record_review_decision(
        db,
        item,
        actor=attorney,
        stage="attorney",
        decision="approve",
        override=True,
    )
    assert event_type == "attorney_override_approved"
    assert item.review_stage == "approved" and item.attorney_override is True

    item.review_stage = "attorney_pending"
    item.reviewer_user_id = attorney.id
    item.staff_reviewed_at = datetime.now(timezone.utc)
    item.staff_reviewed_by_user_id = staff.id
    event_type = await record_review_decision(
        db,
        item,
        actor=attorney,
        stage="attorney",
        decision="request_changes",
    )
    assert event_type == "attorney_changes_requested"
    assert item.review_stage == "staff"
    assert item.reviewer_user_id == staff.id
    assert item.staff_reviewed_at is None
    assert item.attorney_approved_at is None
    assert item.attorney_override is False
