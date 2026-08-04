"""Database-independent checks for the canonical task workflow state machine."""

import uuid
from datetime import date, datetime, timezone

import pytest

from app.models.task import Task, TaskEvent
from app.services.task_workflow import (
    TaskVersionConflict,
    TaskWorkflowError,
    transition_task,
)


class RecordingSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


def make_task(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "title": "Review discovery responses",
        "status": "pending",
        "priority": "high",
        "task_type": "review",
        "version": 3,
        "status_changed_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return Task(**values)


def test_waiting_requires_a_reason_and_does_not_mutate_on_failure():
    db = RecordingSession()
    item = make_task()

    with pytest.raises(TaskWorkflowError, match="waiting reason"):
        transition_task(
            db,
            item,
            to_status="waiting",
            actor_user_id=uuid.uuid4(),
            expected_version=3,
        )

    assert item.status == "pending"
    assert item.version == 3
    assert db.added == []


def test_waiting_transition_records_tickler_and_audit_event():
    db = RecordingSession()
    item = make_task()
    actor_id = uuid.uuid4()

    changed = transition_task(
        db,
        item,
        to_status="waiting",
        actor_user_id=actor_id,
        expected_version=3,
        reason="Waiting for signed medical authorization",
        waiting_follow_up_date=date(2026, 8, 12),
    )

    assert changed is True
    assert item.status == "waiting"
    assert item.waiting_reason == "Waiting for signed medical authorization"
    assert item.waiting_follow_up_date == date(2026, 8, 12)
    assert item.version == 4
    event = db.added[0]
    assert isinstance(event, TaskEvent)
    assert event.event_type == "status_changed"
    assert event.from_status == "pending"
    assert event.to_status == "waiting"
    assert event.actor_user_id == actor_id


def test_completion_and_reopen_keep_closure_invariants():
    db = RecordingSession()
    item = make_task(status="review")
    actor_id = uuid.uuid4()

    transition_task(
        db,
        item,
        to_status="completed",
        actor_user_id=actor_id,
        reason="Final pleading approved and filed",
    )
    assert item.completed_at is not None
    assert item.closed_by_user_id == actor_id
    assert item.closed_reason == "Final pleading approved and filed"
    assert db.added[-1].event_type == "completed"

    transition_task(
        db,
        item,
        to_status="in_progress",
        actor_user_id=actor_id,
        expected_version=4,
    )
    assert item.completed_at is None
    assert item.closed_by_user_id is None
    assert item.closed_reason is None
    assert db.added[-1].event_type == "reopened"


def test_stale_version_is_rejected_before_mutation():
    db = RecordingSession()
    item = make_task(version=9)

    with pytest.raises(TaskVersionConflict):
        transition_task(
            db,
            item,
            to_status="in_progress",
            actor_user_id=uuid.uuid4(),
            expected_version=8,
        )

    assert item.status == "pending"
    assert item.version == 9
    assert db.added == []


def test_leaving_waiting_clears_waiting_metadata():
    db = RecordingSession()
    item = make_task(
        status="waiting",
        waiting_reason="Court order",
        waiting_follow_up_date=date(2026, 8, 8),
    )

    transition_task(
        db,
        item,
        to_status="review",
        actor_user_id=uuid.uuid4(),
        reviewer_user_id=uuid.uuid4(),
    )

    assert item.waiting_reason is None
    assert item.waiting_follow_up_date is None
    assert item.reviewer_user_id is not None
