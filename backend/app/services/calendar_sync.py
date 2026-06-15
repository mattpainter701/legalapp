import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import set_tenant_context
from app.services.token_vault import get_fresh_token, get_fresh_user_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GOOGLE_CAL_BASE = "https://www.googleapis.com/calendar/v3"


class CalendarSyncService:
    async def ms_get_events(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None = None,
        days_ahead: int = 30,
    ) -> list[dict]:
        if user_id:
            token = await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
        else:
            token = await get_fresh_token(db, tenant_id, "microsoft")

        if not token:
            logger.warning(
                "ms_get_events: no Microsoft token for user_id=%s tenant_id=%s",
                user_id,
                tenant_id,
            )
            raise ValueError(
                "No Microsoft calendar token. Please reconnect your calendar in Settings."
            )

        cal_url = f"{GRAPH_BASE}/me/calendarview"
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days_ahead)

        params = {
            "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$select": "id,subject,start,end,location,bodyPreview,organizer,attendees",
            "$top": 100,
            "$orderby": "start/dateTime",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                cal_url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            if resp.status_code != 200:
                raise ValueError(
                    f"Microsoft calendar read failed (HTTP {resp.status_code}). Please try again or reconnect your calendar in Settings."
                )

            events = []
            for evt in resp.json().get("value", []):
                events.append(
                    {
                        "id": evt.get("id"),
                        "provider": "microsoft",
                        "subject": evt.get("subject", ""),
                        "start": evt.get("start", {}).get("dateTime"),
                        "end": evt.get("end", {}).get("dateTime"),
                        "location": (evt.get("location", {}) or {}).get(
                            "displayName", ""
                        ),
                        "body": (evt.get("bodyPreview") or "")[:500],
                        "organizer": (evt.get("organizer", {}) or {})
                        .get("emailAddress", {})
                        .get("name", ""),
                        "attendees": [
                            {
                                "name": a.get("emailAddress", {}).get("name", ""),
                                "email": a.get("emailAddress", {}).get("address", ""),
                            }
                            for a in (evt.get("attendees") or [])
                        ],
                    }
                )
            return events

    async def ms_create_event(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None,
        subject: str,
        start_dt: datetime,
        end_dt: datetime,
        body: str = "",
        location: str = "",
        attendees: list[str] | None = None,
    ) -> dict | None:
        if user_id:
            token = await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
        else:
            token = await get_fresh_token(db, tenant_id, "microsoft")

        if not token:
            return None

        event = {
            "subject": subject,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": "America/New_York",
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": "America/New_York",
            },
        }
        if body:
            event["body"] = {"contentType": "text", "content": body}
        if location:
            event["location"] = {"displayName": location}
        if attendees:
            event["attendees"] = [
                {"emailAddress": {"address": a}, "type": "required"} for a in attendees
            ]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GRAPH_BASE}/me/events",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=event,
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "MS Calendar create event failed: %s %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            return resp.json()

    async def ms_create_scheduled_event(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None,
        *,
        subject: str,
        start_dt: datetime,
        end_dt: datetime,
        timezone_name: str = "UTC",
        body: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        teams_online: bool = False,
    ) -> dict | None:
        """Create a timed Outlook calendar event, optionally as a Teams meeting."""
        token = (
            await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
            if user_id
            else await get_fresh_token(db, tenant_id, "microsoft")
        )
        if not token:
            raise ValueError(
                "No Microsoft calendar token. Please reconnect your calendar in Settings."
            )

        event = {
            "subject": subject,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": timezone_name or "UTC",
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": timezone_name or "UTC",
            },
        }
        if body:
            event["body"] = {"contentType": "text", "content": body}
        if location:
            event["location"] = {"displayName": location}
        if attendees:
            event["attendees"] = [
                {"emailAddress": {"address": a}, "type": "required"} for a in attendees
            ]
        if teams_online:
            event["isOnlineMeeting"] = True
            event["onlineMeetingProvider"] = "teamsForBusiness"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GRAPH_BASE}/me/events",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=event,
            )
            if resp.status_code not in (200, 201):
                raise ValueError(
                    f"Microsoft calendar event create failed (HTTP {resp.status_code})."
                )
            return resp.json()

    async def ms_delete_event(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str | None,
        event_id: str | None,
    ) -> bool:
        if not event_id:
            return False
        token = (
            await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
            if user_id
            else await get_fresh_token(db, tenant_id, "microsoft")
        )
        if not token:
            return False
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{GRAPH_BASE}/me/events/{event_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        return resp.status_code in (200, 202, 204, 404)

    async def google_get_events(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        days_ahead: int = 30,
    ) -> list[dict]:
        token = await get_fresh_user_token(db, tenant_id, user_id, "google")
        if not token:
            logger.warning(
                "google_get_events: no Google token for user_id=%s tenant_id=%s",
                user_id,
                tenant_id,
            )
            raise ValueError(
                "No Google calendar token. Please reconnect your calendar in Settings."
            )

        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "maxResults": 100,
            "singleEvents": "true",
            "orderBy": "startTime",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GOOGLE_CAL_BASE}/calendars/primary/events",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            if resp.status_code != 200:
                raise ValueError(
                    f"Google Calendar read failed (HTTP {resp.status_code}). Please try again or reconnect your calendar in Settings."
                )

            events = []
            for evt in resp.json().get("items", []):
                events.append(
                    {
                        "id": evt.get("id"),
                        "provider": "google",
                        "subject": evt.get("summary", ""),
                        "start": evt.get("start", {}).get("dateTime")
                        or evt.get("start", {}).get("date"),
                        "end": evt.get("end", {}).get("dateTime")
                        or evt.get("end", {}).get("date"),
                        "location": evt.get("location", ""),
                        "body": (evt.get("description") or "")[:500],
                        "organizer": (evt.get("organizer", {}) or {}).get(
                            "displayName", ""
                        ),
                        "attendees": [
                            {
                                "name": a.get("displayName", ""),
                                "email": a.get("email", ""),
                            }
                            for a in (evt.get("attendees") or [])
                        ],
                    }
                )
            return events

    async def google_create_event(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        subject: str,
        start_dt: datetime,
        end_dt: datetime,
        body: str = "",
        location: str = "",
        attendees: list[str] | None = None,
    ) -> dict | None:
        token = await get_fresh_user_token(db, tenant_id, user_id, "google")
        if not token:
            return None

        event = {
            "summary": subject,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": "America/New_York",
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": "America/New_York",
            },
        }
        if body:
            event["description"] = body
        if location:
            event["location"] = location
        if attendees:
            event["attendees"] = [{"email": a} for a in attendees]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GOOGLE_CAL_BASE}/calendars/primary/events",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=event,
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "Google Calendar create event failed: %s %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            return resp.json()

    async def google_create_scheduled_event(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        *,
        subject: str,
        start_dt: datetime,
        end_dt: datetime,
        timezone_name: str = "UTC",
        body: str = "",
        location: str = "",
        attendees: list[str] | None = None,
    ) -> dict | None:
        """Create a timed Google Calendar event."""
        token = await get_fresh_user_token(db, tenant_id, user_id, "google")
        if not token:
            raise ValueError(
                "No Google calendar token. Please reconnect your calendar in Settings."
            )

        event = {
            "summary": subject,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": timezone_name or "UTC",
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": timezone_name or "UTC",
            },
        }
        if body:
            event["description"] = body
        if location:
            event["location"] = location
        if attendees:
            event["attendees"] = [{"email": a} for a in attendees]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{GOOGLE_CAL_BASE}/calendars/primary/events",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=event,
            )
            if resp.status_code not in (200, 201):
                raise ValueError(
                    f"Google Calendar event create failed (HTTP {resp.status_code})."
                )
            return resp.json()

    async def google_delete_event(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        event_id: str | None,
    ) -> bool:
        if not event_id:
            return False
        token = await get_fresh_user_token(db, tenant_id, user_id, "google")
        if not token:
            return False
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{GOOGLE_CAL_BASE}/calendars/primary/events/{event_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        return resp.status_code in (200, 202, 204, 410)

    async def sync_deadlines_to_calendar(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        provider: str,
    ) -> dict:
        from app.models.plugin import Matter

        await set_tenant_context(db, tenant_id)
        result = await db.execute(
            select(Matter).where(
                Matter.tenant_id == tenant_id,
                ~Matter.is_closed,
            )
        )
        matters = result.scalars().all()

        created = 0
        today = date.today()
        cutoff = today + timedelta(days=90)

        for matter in matters:
            key_dates = matter.key_dates or {}
            for label, date_val in key_dates.items():
                if not date_val:
                    continue
                try:
                    if isinstance(date_val, str):
                        d = date.fromisoformat(date_val)
                    elif isinstance(date_val, date):
                        d = date_val
                    else:
                        continue
                except ValueError:
                    continue

                if today <= d <= cutoff:
                    start_dt = datetime(d.year, d.month, d.day, 9, 0, 0)
                    end_dt = datetime(d.year, d.month, d.day, 9, 30, 0)
                    subject = f"[Clarity] {label}: {matter.matter_name}"
                    body = f"Matter: {matter.matter_name}\nType: {matter.matter_type}\nStatus: {matter.status}\nDeadline: {label}"

                    try:
                        if provider == "microsoft":
                            result_ev = await self.ms_create_event(
                                db, tenant_id, user_id, subject, start_dt, end_dt, body
                            )
                        else:
                            result_ev = await self.google_create_event(
                                db, tenant_id, user_id, subject, start_dt, end_dt, body
                            )
                        if result_ev:
                            created += 1
                    except Exception as exc:
                        logger.warning(
                            "Failed to create calendar event for matter %s: %s",
                            matter.id,
                            exc,
                        )

        return {"created": created, "provider": provider}


calendar_sync = CalendarSyncService()
