"""Push-sync tasks and matter key-dates to Microsoft Outlook via Graph."""

import logging
from datetime import date, timedelta

import httpx

from app.config import get_settings
from app.database import async_session_maker
from app.services.token_vault import get_fresh_token, get_fresh_user_token

settings = get_settings()
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Fixed GUID namespace for the Graph single-value extended property that carries
# the LawHand task id. Must never change, or dedupe lookups will break.
CLARITY_TASK_PROP_GUID = "b7d271f9-3a4e-4f6c-9d5a-2c8e1f0a6b3d"
CLARITY_TASK_PROP_ID = f"String {{{CLARITY_TASK_PROP_GUID}}} Name clarity_task_id"


async def _get_token(tenant_id: str, user_id: str | None = None) -> str | None:
    try:
        async with async_session_maker() as db:
            if user_id:
                token = await get_fresh_user_token(db, tenant_id, user_id, "microsoft")
                if token:
                    return token
            return await get_fresh_token(db, tenant_id, "microsoft")
    except Exception:
        logger.warning(
            "Failed to get Microsoft token for tenant %s user %s",
            tenant_id,
            user_id,
            exc_info=True,
        )
        return None


async def _find_event_ids(
    client: httpx.AsyncClient, headers: dict, task_id: str
) -> list[str]:
    """Look up existing Outlook events tagged with this clarity task id."""
    resp = await client.get(
        f"{GRAPH_BASE}/me/events",
        headers=headers,
        params={
            "$filter": (
                "singleValueExtendedProperties/Any("
                f"ep: ep/id eq '{CLARITY_TASK_PROP_ID}' "
                f"and ep/value eq '{task_id}')"
            ),
            "$select": "id",
            "$expand": (
                "singleValueExtendedProperties("
                f"$filter=id eq '{CLARITY_TASK_PROP_ID}')"
            ),
        },
    )
    if resp.status_code != 200:
        logger.warning(
            "Outlook event lookup failed for task %s: %s %s",
            task_id,
            resp.status_code,
            resp.text[:200],
        )
        return []
    return [item["id"] for item in resp.json().get("value", []) if item.get("id")]


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
    """Create or update an Outlook calendar event for a task.

    Uses a Graph single-value extended property carrying ``clarity_task_id``
    to find existing events so we don't create duplicates on re-sync.
    """
    if not title:
        return None

    token = await _get_token(tenant_id, user_id)
    if not token:
        logger.warning(
            "No Microsoft token for tenant %s — skipping calendar push", tenant_id
        )
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Build event subject
    if is_completed:
        subject = f"[DONE] {title}"
    elif matter_name:
        subject = f"{title} — {matter_name}"
    else:
        subject = title

    # All-day Graph events require an exclusive end date (start + 1 day)
    start_day = date.fromisoformat(due_date)
    end_day = start_day + timedelta(days=1)

    event_body = {
        "subject": subject,
        "body": {"contentType": "text", "content": description or title},
        "isAllDay": True,
        "start": {
            "dateTime": f"{start_day.isoformat()}T00:00:00",
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": f"{end_day.isoformat()}T00:00:00",
            "timeZone": "UTC",
        },
        "singleValueExtendedProperties": [
            {"id": CLARITY_TASK_PROP_ID, "value": task_id}
        ],
    }

    if is_completed:
        event_body["categories"] = ["Green category"]

    async with httpx.AsyncClient() as client:
        event_ids = await _find_event_ids(client, headers, task_id)
        event_id = event_ids[0] if event_ids else None

        if event_id:
            resp = await client.patch(
                f"{GRAPH_BASE}/me/events/{event_id}",
                headers=headers,
                json=event_body,
            )
        else:
            resp = await client.post(
                f"{GRAPH_BASE}/me/events",
                headers=headers,
                json=event_body,
            )

        if resp.status_code in (200, 201):
            result = resp.json()
            logger.info(
                "Outlook Calendar %s event %s for task %s",
                "updated" if event_id else "created",
                result.get("id", "?"),
                task_id,
            )
            return result
        else:
            logger.warning(
                "Outlook Calendar push failed for task %s: %s %s",
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
    """Remove the Outlook calendar event for a cancelled/deleted task."""
    token = await _get_token(tenant_id, user_id)
    if not token:
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        event_ids = await _find_event_ids(client, headers, task_id)
        for event_id in event_ids:
            await client.delete(
                f"{GRAPH_BASE}/me/events/{event_id}",
                headers=headers,
            )
            logger.info(
                "Deleted Outlook Calendar event %s for task %s",
                event_id,
                task_id,
            )
        return bool(event_ids)
