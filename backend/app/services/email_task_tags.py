"""Deterministic email subject tags for task creation.

Email bodies and model classifications are useful triage signals, but they are
not a safe instruction channel for creating legal work.  This module keeps the
automatic boundary explicit: only a subject that starts with ``[TASK]`` or
``[DEADLINE]`` can create a task.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from dateutil import parser as dateutil_parser
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.services.task_workflow import append_task_event


_TAG_RE = re.compile(
    r"^\s*\[(?P<tag>task|deadline)"
    r"(?:\s+due\s*=\s*(?P<explicit_due>[^\]]+))?\]"
    r"\s*(?P<title>\S.*?)\s*$",
    re.IGNORECASE,
)
_TRAILING_RELATIVE_RE = re.compile(
    r"(?:\s+|^)(?:due\s+)?in\s+"
    r"(?P<count>\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve)\s+(?P<unit>day|days|week|weeks)\s*[.!?]*$",
    re.IGNORECASE,
)
_TRAILING_TOMORROW_RE = re.compile(
    r"(?:\s+|^)(?:due\s+)?tomorrow\s*[.!?]*$", re.IGNORECASE
)
_TRAILING_DATE_RE = re.compile(
    r"(?:\s+|^)(?:on|by|due(?:\s+on)?)\s+"
    r"(?P<due>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\s*[.!?]*$",
    re.IGNORECASE,
)
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


@dataclass(frozen=True)
class EmailTaskSuggestion:
    """A bounded task instruction derived from an explicit subject tag."""

    tag: str
    title: str
    task_type: str
    priority: str
    due_date: date | None
    due_expression: str | None = None


def _parse_explicit_date(value: str) -> date | None:
    clean = value.strip()
    try:
        return date.fromisoformat(clean)
    except ValueError:
        pass
    for pattern in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(clean, pattern).date()
        except ValueError:
            continue
    return None


def _task_type(tag: str, title: str) -> str:
    if tag == "deadline":
        return "deadline"
    normalized = title.lower()
    if re.search(r"\bcall\b", normalized):
        return "call"
    if re.search(r"\b(file|filing|submit)\b", normalized):
        return "filing"
    if re.search(r"\breview\b", normalized):
        return "review"
    if re.search(r"\b(meet|meeting|follow[ -]?up)\b", normalized):
        return "follow_up"
    return "general"


def _relative_due_date(title: str, base_date: date) -> tuple[date | None, str, str | None]:
    relative = _TRAILING_RELATIVE_RE.search(title)
    if relative:
        raw_count = relative.group("count").lower()
        count = int(raw_count) if raw_count.isdigit() else _WORD_NUMBERS[raw_count]
        unit = relative.group("unit").lower()
        days = count * (7 if unit.startswith("week") else 1)
        clean_title = title[: relative.start()].rstrip(" \t,;:-")
        return base_date + timedelta(days=days), clean_title, relative.group(0).strip()

    tomorrow = _TRAILING_TOMORROW_RE.search(title)
    if tomorrow:
        clean_title = title[: tomorrow.start()].rstrip(" \t,;:-")
        return base_date + timedelta(days=1), clean_title, tomorrow.group(0).strip()

    trailing_date = _TRAILING_DATE_RE.search(title)
    if trailing_date:
        parsed = _parse_explicit_date(trailing_date.group("due"))
        if parsed is not None:
            clean_title = title[: trailing_date.start()].rstrip(" \t,;:-")
            return parsed, clean_title, trailing_date.group(0).strip()

    return None, title.strip(), None


def parse_email_task_tag(
    subject: str | None,
    *,
    received_at: datetime | date | None = None,
) -> EmailTaskSuggestion | None:
    """Parse a leading task tag without interpreting arbitrary email text.

    Supported forms include::

        [TASK] Nigel I need to meet with you in two weeks
        [TASK due=2026-09-09] Meet with Nigel
        [DEADLINE] File response by 09/15/2026

    Replies and forwards do not match because the tag must be the first token.
    Ambiguous dates (for example, "next Friday") deliberately remain unset.
    """

    match = _TAG_RE.fullmatch(subject or "")
    if match is None:
        return None

    tag = match.group("tag").lower()
    raw_title = match.group("title").strip()
    base_date = (
        received_at
        if isinstance(received_at, date) and not isinstance(received_at, datetime)
        else (received_at or datetime.now(timezone.utc)).date()
    )

    explicit_due = match.group("explicit_due")
    if explicit_due is not None:
        due_date = _parse_explicit_date(explicit_due)
        clean_title = raw_title
        due_expression = explicit_due.strip() if due_date is not None else None
    else:
        due_date, clean_title, due_expression = _relative_due_date(raw_title, base_date)

    clean_title = clean_title.strip() or "Follow up on inbound email"
    return EmailTaskSuggestion(
        tag=tag,
        title=clean_title[:500],
        task_type=_task_type(tag, clean_title),
        priority="high" if tag == "deadline" else "medium",
        due_date=due_date,
        due_expression=due_expression,
    )


def email_received_at(email: dict, *, fallback: datetime | None = None) -> datetime:
    """Return a provider-neutral received timestamp for relative date math."""

    for key in ("received", "receivedDateTime", "date", "occurred_at"):
        value = email.get(key)
        if isinstance(value, datetime):
            parsed = value
        elif value:
            try:
                parsed = parsedate_to_datetime(str(value))
            except (TypeError, ValueError, OverflowError):
                try:
                    parsed = dateutil_parser.isoparse(str(value))
                except (TypeError, ValueError, OverflowError):
                    continue
        else:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return fallback or datetime.now(timezone.utc)


async def add_tagged_email_task(
    db: AsyncSession,
    *,
    suggestion: EmailTaskSuggestion,
    tenant_id: uuid.UUID,
    matter_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    external_ref: str | None,
    original_subject: str,
    received_at: datetime,
) -> Task:
    """Add an assigned task and audit events to the caller's transaction."""

    normalized_received_at = received_at
    if normalized_received_at.tzinfo is None:
        normalized_received_at = normalized_received_at.replace(tzinfo=timezone.utc)
    description_lines = [
        f"Created from a reviewed [{suggestion.tag.upper()}] email subject tag.",
        f"Original subject: {original_subject[:500]}",
        f"Email received: {normalized_received_at.astimezone(timezone.utc).isoformat()}",
    ]
    if suggestion.due_expression:
        description_lines.append(f"Due expression: {suggestion.due_expression}")

    task = Task(
        tenant_id=tenant_id,
        title=suggestion.title,
        description="\n".join(description_lines),
        task_type=suggestion.task_type,
        priority=suggestion.priority,
        due_date=suggestion.due_date,
        matter_id=matter_id,
        assigned_to_user_id=actor_user_id,
        created_by_user_id=actor_user_id,
        source="email_subject_tag",
        external_ref=external_ref,
    )
    db.add(task)
    await db.flush()
    append_task_event(
        db,
        task,
        event_type="created",
        actor_user_id=actor_user_id,
        to_status="pending",
        metadata={"source": "email_subject_tag", "tag": suggestion.tag},
    )
    append_task_event(
        db,
        task,
        event_type="assigned",
        actor_user_id=actor_user_id,
        metadata={"assigned_to_user_id": str(actor_user_id)},
    )
    return task
