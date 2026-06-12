"""Push-sync tasks and matter key-dates to Google Calendar."""

import logging

import httpx

from app.config import get_settings
from app.database import async_session_maker
from app.services.token_vault import get_fresh_token, get_fresh_user_token

settings = get_settings()
logger = logging.getLogger(__name__)

CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


async def _get_token(tenant_id: str, user_id: str | None = None) -> str | None:
    try:
        async with async_session_maker() as db:
            if user_id:
                token = await get_fresh_user_token(db, tenant_id, user_id, "google")
                if token:
                    return token
            return await get_fresh_token(db, tenant_id, "google")
    except Exception:
        logger.warning(
            "Failed to get Google token for tenant %s user %s",
            tenant_id,
            user_id,
            exc_info=True,
        )
        return None


async def upsert_task_event(
    tenant_id: str,
    task_id: str,
    title: str,
    due_date: str,
    *,
    description: str = "",
    matter_name: str = "",
    is_completed: bool = False,
    user_id: str | None = None,
) -> dict | None:
    """Create or update a Google Calendar event for a task.

    Uses an extended-property marker ``clarity_task_id`` to find existing events
    so we don't create duplicates on re-sync.
    """
    if not title:
        return None

    token = await _get_token(tenant_id, user_id)
    if not token:
        logger.warning(
            "No Google token for tenant %s — skipping calendar push", tenant_id
        )
        return None

    headers = {"Authorization": f"Bearer {token}"}
    event_id = None

    # Search for existing event via extended property
    async with httpx.AsyncClient() as client:
        search_resp = await client.get(
            f"{CALENDAR_BASE}/calendars/primary/events",
            headers=headers,
            params={
                "privateExtendedProperty": f"clarity_task_id={task_id}",
                "showDeleted": "false",
            },
        )
        if search_resp.status_code == 200:
            items = search_resp.json().get("items", [])
            if items:
                event_id = items[0]["id"]

    # Build event body
    if is_completed:
        summary = f"[DONE] {title}"
    elif matter_name:
        summary = f"{title} — {matter_name}"
    else:
        summary = title

    event_body = {
        "summary": summary,
        "description": description or title,
        "start": {"date": due_date},
        "end": {"date": due_date},
        "extendedProperties": {
            "private": {
                "clarity_task_id": task_id,
            }
        },
    }

    if is_completed:
        event_body["colorId"] = "10"  # green in Google Calendar

    async with httpx.AsyncClient() as client:
        if event_id:
            resp = await client.patch(
                f"{CALENDAR_BASE}/calendars/primary/events/{event_id}",
                headers=headers,
                json=event_body,
            )
        else:
            resp = await client.post(
                f"{CALENDAR_BASE}/calendars/primary/events",
                headers=headers,
                json=event_body,
            )

        if resp.status_code in (200, 201):
            result = resp.json()
            logger.info(
                "Google Calendar %s event %s for task %s",
                "updated" if event_id else "created",
                result.get("id", "?"),
                task_id,
            )
            return result
        else:
            logger.warning(
                "Google Calendar push failed for task %s: %s %s",
                task_id,
                resp.status_code,
                resp.text[:200],
            )
            return None


async def delete_task_event(
    tenant_id: str,
    task_id: str,
    user_id: str | None = None,
) -> bool:
    """Remove the Google Calendar event for a cancelled/deleted task."""
    token = await _get_token(tenant_id, user_id)
    if not token:
        return False

    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient() as client:
        search_resp = await client.get(
            f"{CALENDAR_BASE}/calendars/primary/events",
            headers=headers,
            params={
                "privateExtendedProperty": f"clarity_task_id={task_id}",
                "showDeleted": "false",
            },
        )
        if search_resp.status_code != 200:
            return False
        items = search_resp.json().get("items", [])
        for item in items:
            await client.delete(
                f"{CALENDAR_BASE}/calendars/primary/events/{item['id']}",
                headers=headers,
            )
            logger.info(
                "Deleted Google Calendar event %s for task %s",
                item["id"],
                task_id,
            )
        return bool(items)
