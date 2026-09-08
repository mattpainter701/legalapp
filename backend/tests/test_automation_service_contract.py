from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.automation_service_contract import (
    ServiceIdentityInput,
    ServiceSchedule,
    due_occurrence,
)


def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "capabilities",
    [
        ["approve_task"],
        ["propose_client_email"],
        ["search_clients"],
        ["propose_task", "propose_task"],
    ],
)
def test_service_grants_are_narrow(capabilities):
    with pytest.raises(ValidationError):
        ServiceIdentityInput(name="Night preparation", capabilities=capabilities)


def test_named_service_grant_is_trimmed():
    identity = ServiceIdentityInput(
        name=" Night preparation ", capabilities=["propose_task"]
    )
    assert identity.name == "Night preparation"
    with pytest.raises(ValidationError):
        ServiceIdentityInput(name=" ", capabilities=["propose_task"])


@pytest.mark.parametrize(
    "values",
    [
        {"kind": "daily"},
        {"kind": "daily", "local_time": "25:00"},
        {"kind": "daily", "local_time": "02:00", "timezone": "not/a-zone"},
        {"kind": "weekly", "local_time": "02:00", "weekdays": []},
        {"kind": "weekly", "local_time": "02:00", "weekdays": [7]},
        {"kind": "daily", "local_time": "02:00", "weekdays": [1]},
        {"kind": "workflow_event"},
        {
            "kind": "workflow_event",
            "event_rule_id": str(uuid4()),
            "local_time": "02:00",
        },
        {"kind": "daily", "local_time": "02:00", "event_rule_id": str(uuid4())},
    ],
)
def test_only_fixed_schedules_are_accepted(values):
    with pytest.raises(ValidationError):
        ServiceSchedule(**values)


def test_due_occurrence_has_no_unbounded_backlog_or_dst_duplicate():
    schedule = ServiceSchedule(
        kind="daily", local_time="02:00", timezone="America/Chicago"
    )
    approved = moment("2026-01-01T00:00:00")
    assert due_occurrence(schedule, moment("2026-03-08T07:59:00"), approved) is None
    assert (
        due_occurrence(schedule, moment("2026-03-08T08:00:00"), approved)
        == "date:2026-03-08"
    )
    fall = ServiceSchedule(kind="daily", local_time="01:30", timezone="America/Chicago")
    assert due_occurrence(
        fall, moment("2026-11-01T06:30:00"), approved
    ) == due_occurrence(fall, moment("2026-11-01T07:30:00"), approved)
    after_due = moment("2026-09-08T02:01:00")
    assert (
        due_occurrence(
            ServiceSchedule(kind="daily", local_time="02:00"),
            moment("2026-09-08T03:00:00"),
            after_due,
        )
        is None
    )


def test_due_occurrence_requires_timezone_aware_timestamps():
    schedule = ServiceSchedule(kind="daily", local_time="02:00")
    now = datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        due_occurrence(schedule, datetime(2026, 1, 1, 3, 0, 0), now)
    with pytest.raises(ValueError):
        due_occurrence(schedule, now, datetime(2026, 1, 1, 0, 0, 0))


def test_due_occurrence_ignores_event_schedules():
    schedule = ServiceSchedule(kind="workflow_event", event_rule_id=uuid4())
    now = datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
    assert due_occurrence(schedule, now, now) is None


def test_due_occurrence_skips_wrong_weekday_and_unapproved_date():
    weekly = ServiceSchedule(
        kind="weekly", local_time="02:00", weekdays=[1], timezone="UTC"
    )
    approved = moment("2026-01-04T00:00:00")  # Sunday
    # 2026-01-05 is Monday, which is not in weekdays=[1] (Tuesday)
    assert due_occurrence(weekly, moment("2026-01-05T03:00:00"), approved) is None

    # Approval local date is ahead of now's local date.
    assert (
        due_occurrence(
            ServiceSchedule(kind="daily", local_time="02:00", timezone="UTC"),
            moment("2026-01-01T23:00:00"),
            moment("2026-01-02T01:00:00"),
        )
        is None
    )

    # Same approval date with approval already at/after the scheduled time.
    assert (
        due_occurrence(
            ServiceSchedule(kind="daily", local_time="02:00", timezone="UTC"),
            moment("2026-01-02T03:00:00"),
            moment("2026-01-02T02:30:00"),
        )
        is None
    )
