"""Mapped history-only CSV ingestion; unrelated source columns are discarded."""

import csv
import hashlib
import io
from collections import Counter

from app.services.workflow_observations import (
    ASSIGNEE_ROLES,
    TASK_TYPES,
    as_date,
    task_label,
)

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
FIELDS = frozenset(
    {
        "matter_key",
        "title",
        "opened_at",
        "due_date",
        "matter_type",
        "practice_area",
        "matter_name",
        "assignee_role",
        "task_type",
    }
)


def parse_csv(content: bytes):
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Each history CSV must be between 1 byte and 2 MiB")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        headers = reader.fieldnames or []
        if not headers or len(headers) > 100 or len(set(headers)) != len(headers):
            raise ValueError("CSV headers must be present and unique (at most 100)")
        if any(not header.strip() or len(header) > 200 for header in headers):
            raise ValueError("CSV headers must be nonempty and at most 200 characters")
        rows = []
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise ValueError("Each history CSV supports at most 5,000 rows")
            if None in row:
                raise ValueError("A CSV row has more values than its header")
            rows.append(row)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("Upload a UTF-8 CSV export") from exc
    return headers, rows


def file_fingerprint(tasks: bytes, matters: bytes | None):
    return hashlib.sha256(
        len(tasks).to_bytes(8, "big") + tasks + (matters or b"")
    ).hexdigest()


def normalize_history(task_rows, matter_rows, task_headers, matter_headers, mapping):
    if set(mapping) - {"tasks", "matters"}:
        raise ValueError("Unknown history mapping section")
    task_map = mapping.get("tasks") or {}
    matter_map = mapping.get("matters") or {}
    for columns, headers in ((task_map, task_headers), (matter_map, matter_headers)):
        if set(columns) - FIELDS or any(
            not isinstance(column, str) or column not in headers
            for column in columns.values()
            if column
        ):
            raise ValueError(
                "Mappings must reference supported fields and existing CSV headers"
            )
    if any(not task_map.get(field) for field in ("matter_key", "title", "due_date")):
        raise ValueError("Map the task matter key, title, and due date")
    if not task_map.get("opened_at") and not (
        matter_map.get("matter_key") and matter_map.get("opened_at")
    ):
        raise ValueError(
            "Map the matter-open date in the task CSV or a matching matter CSV"
        )
    by_matter = {}
    for row in matter_rows:
        key = str(row.get(matter_map.get("matter_key")) or "").strip()
        if not key:
            continue
        if key in by_matter:
            raise ValueError(
                "Matter CSV contains duplicate mapped keys; resolve ambiguous matches first"
            )
        by_matter[key] = row
    normalized = []
    skipped = Counter()
    for number, row in enumerate(task_rows, start=2):
        values = {
            field: row.get(column) for field, column in task_map.items() if column
        }
        key = str(values.get("matter_key") or "").strip()
        matter = by_matter.get(key)
        if matter:
            for field, column in matter_map.items():
                if field != "matter_key" and column and not values.get(field):
                    values[field] = matter.get(column)
        anchor, due = as_date(values.get("opened_at")), as_date(values.get("due_date"))
        label = task_label(
            values.get("title"), private_terms=(values.get("matter_name"),)
        )
        if not key or not anchor or not due or not label:
            skipped["missing_or_invalid_mapped_history"] += 1
            continue
        if not 0 <= (due - anchor).days <= 3650:
            skipped["unsupported_timing_offset"] += 1
            continue
        # These are the only persisted fields. Subjects, descriptions, emails,
        # notes and other unmapped export columns never enter the import store.
        normalized.append(
            dict(
                matter_key=key,
                title=label,
                opened_at=anchor.isoformat(),
                due_date=due.isoformat(),
                matter_type=str(values.get("matter_type") or "general")[:100],
                practice_area=str(values["practice_area"])[:200]
                if values.get("practice_area")
                else None,
                assignee_role=values.get("assignee_role")
                if values.get("assignee_role") in ASSIGNEE_ROLES
                else "unassigned",
                task_type=values.get("task_type")
                if values.get("task_type") in TASK_TYPES
                else "general",
                source_row_number=number,
            )
        )
    if not normalized:
        raise ValueError(
            "No usable history rows matched the mapped matter keys and dates"
        )
    return normalized, dict(skipped)
