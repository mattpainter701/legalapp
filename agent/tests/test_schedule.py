"""Per-share scan schedules decide when a share is walked."""

from datetime import datetime, timedelta

import pytest

from clarity_agent.schedule import CronError, due_for_scan, is_due, parse_cron

NOW = datetime(2026, 8, 24, 6, 0)  # a Monday, 06:00


def test_every_six_hours_is_due_at_the_slot_and_not_between():
    assert is_due("0 */6 * * *", datetime(2026, 8, 24, 5, 0), NOW) is True
    assert (
        is_due("0 */6 * * *", datetime(2026, 8, 24, 6, 0), datetime(2026, 8, 24, 7, 0))
        is False
    )


def test_nightly_schedule_waits_for_its_hour():
    # Last run 03:00, now 06:00 — the 02:00 slot has not come round again.
    assert is_due("0 2 * * *", datetime(2026, 8, 24, 3, 0), NOW) is False
    # Last run yesterday at 01:00 — 02:00 fell inside the window.
    assert is_due("0 2 * * *", datetime(2026, 8, 23, 1, 0), NOW) is True


def test_a_share_that_never_ran_is_due():
    assert is_due("0 2 * * *", None, NOW) is True


def test_a_long_gap_is_due_without_walking_every_minute():
    assert is_due("0 2 1 1 *", NOW - timedelta(days=400), NOW) is True


def test_weekday_and_monthday_are_a_union_like_cron():
    schedule = parse_cron("0 3 1 * 1")
    assert schedule.matches(datetime(2026, 8, 3, 3, 0)) is True  # a Monday
    assert schedule.matches(datetime(2026, 9, 1, 3, 0)) is True  # the 1st
    assert schedule.matches(datetime(2026, 9, 2, 3, 0)) is False


def test_lists_ranges_and_steps():
    schedule = parse_cron("0,30 9-17/4 * * *")
    assert schedule.matches(datetime(2026, 8, 24, 9, 30)) is True
    assert schedule.matches(datetime(2026, 8, 24, 13, 0)) is True
    assert schedule.matches(datetime(2026, 8, 24, 10, 0)) is False


@pytest.mark.parametrize(
    "expression",
    ["", "0 2 * *", "0 2 * * 9", "x * * * *", "0 */0 * * *", "0 25 * * *"],
)
def test_unusable_expressions_are_rejected(expression):
    with pytest.raises(CronError):
        parse_cron(expression)


def test_share_without_a_schedule_falls_back_to_the_agent_interval():
    share = {"share_id": "s-1"}

    assert (
        due_for_scan(share, NOW - timedelta(minutes=30), NOW, fallback_minutes=60)
        is False
    )
    assert (
        due_for_scan(share, NOW - timedelta(minutes=90), NOW, fallback_minutes=60)
        is True
    )


def test_unusable_schedule_falls_back_instead_of_never_scanning():
    share = {"share_id": "s-2", "scan_schedule": "not a cron"}

    assert (
        due_for_scan(share, NOW - timedelta(minutes=5), NOW, fallback_minutes=60)
        is False
    )
    assert (
        due_for_scan(share, NOW - timedelta(hours=3), NOW, fallback_minutes=60) is True
    )


def test_schedule_is_honoured_when_it_parses():
    share = {"share_id": "s-3", "scan_schedule": "0 2 * * *"}

    # Not due at 06:00 when it already ran at 03:00, even though the agent's
    # own interval would have fired.
    assert (
        due_for_scan(share, datetime(2026, 8, 24, 3, 0), NOW, fallback_minutes=60)
        is False
    )
