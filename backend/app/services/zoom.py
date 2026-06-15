"""Zoom OAuth-backed meeting creation for scheduled events."""

import logging
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.token_vault import get_fresh_token, get_fresh_user_token

logger = logging.getLogger(__name__)

ZOOM_BASE = "https://api.zoom.us/v2"


class ZoomIntegrationError(RuntimeError):
    """Raised when Zoom is unavailable or rejects a meeting operation."""


async def get_zoom_token(
    db: AsyncSession, tenant_id: str, user_id: str | None = None
) -> str | None:
    """Return a user Zoom token, falling back to tenant-level shared Zoom."""
    if user_id:
        token = await get_fresh_user_token(db, tenant_id, user_id, "zoom")
        if token:
            return token
    return await get_fresh_token(db, tenant_id, "zoom")


async def create_meeting(
    db: AsyncSession,
    tenant_id: str,
    user_id: str | None,
    *,
    topic: str,
    start_at: datetime,
    duration_minutes: int,
    timezone_name: str,
    agenda: str = "",
    attendees: list[str] | None = None,
) -> dict[str, Any]:
    """Create a scheduled Zoom meeting for the connected user/shared account."""
    token = await get_zoom_token(db, tenant_id, user_id)
    if not token:
        raise ZoomIntegrationError("No Zoom token. Connect Zoom before scheduling.")

    settings: dict[str, Any] = {
        "join_before_host": False,
        "waiting_room": True,
    }
    if attendees:
        settings["meeting_invitees"] = [{"email": email} for email in attendees]

    payload = {
        "topic": topic,
        "type": 2,
        "start_time": start_at.isoformat(),
        "duration": max(int(duration_minutes), 1),
        "timezone": timezone_name or "UTC",
        "agenda": agenda[:2000] if agenda else "",
        "settings": settings,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{ZOOM_BASE}/users/me/meetings",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code not in (200, 201):
            logger.warning(
                "Zoom create meeting failed: %s %s", resp.status_code, resp.text[:300]
            )
            raise ZoomIntegrationError(
                f"Zoom meeting creation failed (HTTP {resp.status_code})."
            )

    data = resp.json()
    return {
        "meeting_id": str(data.get("id") or ""),
        "join_url": data.get("join_url") or "",
        "start_url": data.get("start_url"),
        "raw": data,
    }


async def delete_meeting(
    db: AsyncSession,
    tenant_id: str,
    user_id: str | None,
    meeting_id: str | None,
) -> bool:
    """Best-effort delete of a Zoom meeting."""
    if not meeting_id:
        return False
    token = await get_zoom_token(db, tenant_id, user_id)
    if not token:
        return False
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.delete(
            f"{ZOOM_BASE}/meetings/{meeting_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code in (200, 202, 204, 404):
        return True
    logger.warning(
        "Zoom delete meeting failed: %s %s", resp.status_code, resp.text[:200]
    )
    return False
