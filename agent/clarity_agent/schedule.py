"""Per-share scan schedules.

Each share carries a five-field cron expression set in the admin console. The
agent ticks once a minute and asks whether a share is due, rather than running
every share on one global interval — otherwise a share configured for "once a
night" would still be walked every few hours.

Only the subset of cron the console offers is supported: ``*``, ``*/n``,
lists, ranges, and plain numbers, in the standard
``minute hour day-of-month month day-of-week`` order. An expression that does
not parse is reported as such so the caller can fall back to its interval
instead of silently never scanning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger("clarity_agent.schedule")

# Bound the catch-up search. A share whose last scan is older than this is due
# regardless of what its expression says.
MAX_LOOKBACK = timedelta(days=7)

_FIELD_RANGES = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 6),  # day of week (Sunday = 0)
)


class CronError(ValueError):
    """The expression is not one this agent can evaluate."""


@dataclass(frozen=True)
class CronSchedule:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    # Cron treats day-of-month and day-of-week as a union when both are
    # restricted, which is why the wildcard state has to be remembered.
    day_restricted: bool
    weekday_restricted: bool

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes:
            return False
        if moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        # Python: Monday = 0. Cron: Sunday = 0.
        weekday = (moment.weekday() + 1) % 7
        day_ok = moment.day in self.days
        weekday_ok = weekday in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return day_ok or weekday_ok
        return day_ok and weekday_ok


def _parse_field(field: str, low: int, high: int) -> tuple[frozenset[int], bool]:
    """Return the matching values and whether the field restricts anything."""
    values: set[int] = set()
    restricted = False
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"empty field element in {field!r}")
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            try:
                step = int(step_text)
            except ValueError:
                raise CronError(f"invalid step in {field!r}") from None
            if step < 1:
                raise CronError(f"invalid step in {field!r}")
        if part in ("*", ""):
            start, end = low, high
            if step != 1:
                restricted = True
        elif "-" in part:
            start_text, _, end_text = part.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                raise CronError(f"invalid range in {field!r}") from None
            restricted = True
        else:
            try:
                start = end = int(part)
            except ValueError:
                raise CronError(f"invalid value in {field!r}") from None
            restricted = True
        if start < low or end > high or start > end:
            raise CronError(f"value out of range in {field!r}")
        values.update(range(start, end + 1, step))
    if not values:
        raise CronError(f"no values matched in {field!r}")
    return frozenset(values), restricted


def parse_cron(expression: str) -> CronSchedule:
    fields = (expression or "").split()
    if len(fields) != 5:
        raise CronError(f"expected 5 cron fields, got {len(fields)}: {expression!r}")
    parsed = [
        _parse_field(field, low, high)
        for field, (low, high) in zip(fields, _FIELD_RANGES)
    ]
    return CronSchedule(
        minutes=parsed[0][0],
        hours=parsed[1][0],
        days=parsed[2][0],
        months=parsed[3][0],
        weekdays=parsed[4][0],
        day_restricted=parsed[2][1],
        weekday_restricted=parsed[4][1],
    )


def is_due(expression: str, last_run: datetime | None, now: datetime) -> bool:
    """True when a scheduled minute falls in ``(last_run, now]``.

    A share that has never run is due immediately, which is what makes a fresh
    agent index its shares on startup instead of waiting for the next slot.
    """
    if last_run is None:
        return True
    if now <= last_run:
        return False
    if now - last_run > MAX_LOOKBACK:
        return True

    schedule = parse_cron(expression)
    moment = (last_run + timedelta(minutes=1)).replace(second=0, microsecond=0)
    end = now.replace(second=0, microsecond=0)
    while moment <= end:
        if schedule.matches(moment):
            return True
        moment += timedelta(minutes=1)
    return False


def due_for_scan(
    share: dict,
    last_run: datetime | None,
    now: datetime,
    fallback_minutes: int = 360,
) -> bool:
    """Schedule check for one share, tolerant of a missing or bad expression.

    Without a usable expression the share falls back to the agent's own scan
    interval, so a share never ends up either unscanned or scanned every tick.
    """
    if last_run is None:
        return True

    expression = share.get("scan_schedule") or ""
    if expression:
        try:
            return is_due(expression, last_run, now)
        except CronError as exc:
            logger.warning(
                "Share %s has an unusable scan schedule (%s); falling back to the "
                "agent scan interval",
                share.get("share_path") or share.get("share_id"),
                exc,
            )

    return now - last_run >= timedelta(minutes=max(1, fallback_minutes))
