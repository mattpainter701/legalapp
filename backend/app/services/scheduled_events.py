"""Scheduled event orchestration across calendar and meeting providers."""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scheduled_event import ScheduledEvent
from app.services.calendar_sync import calendar_sync
from app.services import zoom as zoom_service


def _duration_minutes(start_at: datetime, end_at: datetime) -> int:
    seconds = max((end_at - start_at).total_seconds(), 60)
    return max(int(seconds // 60), 1)


def _join_attendees(attendees: list[str] | None) -> list[str]:
    return [a.strip() for a in (attendees or []) if isinstance(a, str) and a.strip()]


def _body_with_join(description: str | None, join_url: str | None) -> str:
    body = description or ""
    if join_url:
        body = f"{body}\n\nJoin meeting: {join_url}".strip()
    return body


def _microsoft_join_url(event: dict[str, Any]) -> str | None:
    online = event.get("onlineMeeting") or {}
    if isinstance(online, dict):
        return online.get("joinUrl")
    return None


async def create_external_event(
    db: AsyncSession,
    event: ScheduledEvent,
    *,
    tenant_id: str,
    user_id: str,
) -> ScheduledEvent:
    """Create external Zoom/calendar artifacts for a ScheduledEvent row."""
    attendees = _join_attendees(event.attendees)
    join_url = None
    meeting_id = None
    sync_errors: list[str] = []

    if event.meeting_provider == "zoom":
        try:
            zoom = await zoom_service.create_meeting(
                db,
                tenant_id,
                user_id,
                topic=event.title,
                start_at=event.start_at,
                duration_minutes=_duration_minutes(event.start_at, event.end_at),
                timezone_name=event.timezone,
                agenda=event.description or "",
                attendees=attendees,
            )
            join_url = zoom.get("join_url")
            meeting_id = zoom.get("meeting_id")
        except Exception as exc:
            sync_errors.append(str(exc))

    if event.calendar_provider == "microsoft":
        try:
            result = await calendar_sync.ms_create_scheduled_event(
                db,
                tenant_id,
                user_id,
                subject=event.title,
                start_dt=event.start_at,
                end_dt=event.end_at,
                timezone_name=event.timezone,
                body=_body_with_join(event.description, join_url),
                location=join_url or "",
                attendees=attendees,
                teams_online=event.meeting_provider == "teams",
            )
            if result:
                event.external_calendar_event_id = result.get("id")
                event.external_calendar_url = result.get("webLink") or result.get("webUrl")
                if event.meeting_provider == "teams":
                    online = result.get("onlineMeeting") or {}
                    join_url = _microsoft_join_url(result)
                    meeting_id = online.get("conferenceId")
        except Exception as exc:
            sync_errors.append(str(exc))
    elif event.calendar_provider == "google":
        try:
            result = await calendar_sync.google_create_scheduled_event(
                db,
                tenant_id,
                user_id,
                subject=event.title,
                start_dt=event.start_at,
                end_dt=event.end_at,
                timezone_name=event.timezone,
                body=_body_with_join(event.description, join_url),
                location=join_url or "",
                attendees=attendees,
            )
            if result:
                event.external_calendar_event_id = result.get("id")
                event.external_calendar_url = result.get("htmlLink")
        except Exception as exc:
            sync_errors.append(str(exc))

    event.join_url = join_url or event.join_url
    event.meeting_id = meeting_id or event.meeting_id
    if sync_errors:
        event.sync_status = "error"
        event.sync_error = "; ".join(sync_errors)[:2000]
    else:
        event.sync_status = "synced" if event.calendar_provider or event.join_url else "local"
        event.sync_error = None
    return event


async def delete_external_event(
    db: AsyncSession,
    event: ScheduledEvent,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    """Best-effort delete of external calendar/Zoom artifacts."""
    if event.calendar_provider == "microsoft":
        await calendar_sync.ms_delete_event(
            db, tenant_id, user_id, event.external_calendar_event_id
        )
    elif event.calendar_provider == "google":
        await calendar_sync.google_delete_event(
            db, tenant_id, user_id, event.external_calendar_event_id
        )
    if event.meeting_provider == "zoom":
        await zoom_service.delete_meeting(db, tenant_id, user_id, event.meeting_id)
