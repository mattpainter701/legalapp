from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

REQUIRED_BULK_OBJECTS = (
    "schema",
    "courts",
    "dockets",
    "opinion-clusters",
    "opinions",
    "citations",
    "citation-map",
)

_DATED_KEY_RE = re.compile(
    r"^bulk-data/(?P<name>schema|courts|dockets|opinion-clusters|opinions|citations|citation-map)-(?P<date>\d{4}-\d{2}-\d{2})\.(?P<suffix>sql|csv\.bz2)$"
)


@dataclass(frozen=True)
class BulkSnapshot:
    date: str
    keys: tuple[str, ...]


def required_bulk_keys(snapshot_date: str) -> tuple[str, ...]:
    return tuple(
        f"bulk-data/{name}-{snapshot_date}.sql"
        if name == "schema"
        else f"bulk-data/{name}-{snapshot_date}.csv.bz2"
        for name in REQUIRED_BULK_OBJECTS
    )


def choose_latest_snapshot(keys: Iterable[str]) -> BulkSnapshot:
    by_date: dict[str, set[str]] = {}
    for key in keys:
        match = _DATED_KEY_RE.match(key)
        if not match:
            continue
        by_date.setdefault(match.group("date"), set()).add(key)

    complete_dates = [
        snapshot_date
        for snapshot_date, snapshot_keys in by_date.items()
        if set(required_bulk_keys(snapshot_date)).issubset(snapshot_keys)
    ]
    if not complete_dates:
        raise ValueError("No complete CourtListener bulk snapshot found")

    latest = max(complete_dates, key=date.fromisoformat)
    return BulkSnapshot(date=latest, keys=required_bulk_keys(latest))


def priority_court_ids() -> tuple[str, ...]:
    return (
        "scotus",
        "ca1",
        "ca2",
        "ca3",
        "ca4",
        "ca5",
        "ca6",
        "ca7",
        "ca8",
        "ca9",
        "ca10",
        "ca11",
        "cadc",
        "cafc",
        "dcd",
        "tx",
        "tex",
        "cal",
        "ny",
        "fla",
        "ill",
        "pa",
    )


def federal_appellate_court_ids() -> tuple[str, ...]:
    """Published opinions with nationwide/federal regional research value."""
    return (
        "scotus",
        "ca1",
        "ca2",
        "ca3",
        "ca4",
        "ca5",
        "ca6",
        "ca7",
        "ca8",
        "ca9",
        "ca10",
        "ca11",
        "cadc",
        "cafc",
    )
