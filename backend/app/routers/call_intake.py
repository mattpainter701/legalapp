"""Call intake integrations (Zoom recordings → communication logs)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.communication_log import CommunicationLog
from app.models.tenant_credential import TenantCredential
from app.services.token_vault import decrypt_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/call-intake", tags=["call-intake"])
integrations_router = APIRouter(prefix="/api/integrations/zoom", tags=["call-intake"])


class ZoomSyncResponse(BaseModel):
    provider: str = "zoom"
    scanned: int = 0
    created: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


def _parse_zoom_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Could not parse Zoom recording timestamp: %s", value)
        return datetime.now(timezone.utc)


def _is_fresh(expires_at: datetime | None) -> bool:
    if not expires_at:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < expires_at - timedelta(seconds=60)


def _recording_external_ref(meeting: dict[str, Any], file_info: dict[str, Any]) -> str:
    file_id = file_info.get("id") or file_info.get("recording_start") or "recording"
    meeting_uuid = meeting.get("uuid") or meeting.get("id") or "meeting"
    return f"zoom:{meeting_uuid}:{file_id}"


def _recording_subject(meeting: dict[str, Any]) -> str:
    topic = (meeting.get("topic") or "Zoom call").strip()
    return f"Zoom call: {topic}" if not topic.lower().startswith("zoom") else topic


def _recording_body(meeting: dict[str, Any], file_info: dict[str, Any]) -> str:
    lines = [
        f"Zoom meeting ID: {meeting.get('id') or 'Unknown'}",
        f"Topic: {meeting.get('topic') or 'Untitled'}",
        (
            "Started: "
            f"{meeting.get('start_time') or file_info.get('recording_start') or 'Unknown'}"
        ),
    ]
    duration = meeting.get("duration")
    if duration is not None:
        lines.append(f"Duration: {duration} minute(s)")
    download_url = file_info.get("download_url") or file_info.get("play_url")
    if download_url:
        lines.append(f"Recording URL: {download_url}")
    return "\n".join(lines)


async def _get_zoom_credential(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> TenantCredential | None:
    result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == "zoom",
            TenantCredential.is_active,
        )
    )
    return result.scalars().first()


async def _run_zoom_sync(
    *,
    days: int,
    current_user,
    db: AsyncSession,
) -> ZoomSyncResponse:
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    cred = await _get_zoom_credential(db, tenant_id)
    if not cred:
        raise HTTPException(
            status_code=400,
            detail="Zoom is not connected for this tenant",
        )
    if not _is_fresh(cred.token_expires_at):
        raise HTTPException(
            status_code=400,
            detail="Zoom authorization expired; reconnect Zoom",
        )

    try:
        access_token = decrypt_token(cred.encrypted_access_token)
    except Exception as exc:
        logger.exception("Failed to decrypt Zoom token for tenant %s", tenant_id)
        raise HTTPException(
            status_code=400,
            detail="Zoom connection is invalid; reconnect Zoom",
        ) from exc

    from_date = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    to_date = datetime.now(timezone.utc).date().isoformat()
    params = {"from": from_date, "to": to_date, "page_size": 100}
    headers = {"Authorization": f"Bearer {access_token}"}

    response = ZoomSyncResponse()
    next_page_token = ""

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            request_params = dict(params)
            if next_page_token:
                request_params["next_page_token"] = next_page_token
            try:
                resp = await client.get(
                    "https://api.zoom.us/v2/users/me/recordings",
                    headers=headers,
                    params=request_params,
                )
            except httpx.HTTPError as exc:
                logger.exception(
                    "Zoom recordings request failed for tenant %s",
                    tenant_id,
                )
                raise HTTPException(
                    status_code=502,
                    detail="Zoom recordings request failed",
                ) from exc

            if resp.status_code == 401:
                raise HTTPException(
                    status_code=400,
                    detail="Zoom authorization expired; reconnect Zoom",
                )
            if resp.status_code >= 400:
                logger.warning(
                    "Zoom recordings sync failed: status=%s body=%s",
                    resp.status_code,
                    resp.text[:500],
                )
                raise HTTPException(
                    status_code=502,
                    detail="Zoom recordings sync failed",
                )

            payload = resp.json()
            meetings = payload.get("meetings") or []
            for meeting in meetings:
                files = meeting.get("recording_files") or [{}]
                for file_info in files:
                    file_type = file_info.get("file_type")
                    if file_type and file_type not in {
                        "MP4",
                        "M4A",
                        "TRANSCRIPT",
                        "CHAT",
                    }:
                        continue
                    response.scanned += 1
                    external_ref = _recording_external_ref(meeting, file_info)
                    existing = await db.execute(
                        select(CommunicationLog.id).where(
                            CommunicationLog.tenant_id == tenant_id,
                            CommunicationLog.external_ref == external_ref,
                        )
                    )
                    if existing.scalar_one_or_none():
                        response.skipped += 1
                        continue
                    db.add(
                        CommunicationLog(
                            tenant_id=tenant_id,
                            direction="inbound",
                            channel="call",
                            status="received",
                            subject=_recording_subject(meeting),
                            body=_recording_body(meeting, file_info),
                            summary="Imported from Zoom cloud recording sync.",
                            created_by_user_id=current_user.id,
                            occurred_at=_parse_zoom_time(
                                meeting.get("start_time")
                                or file_info.get("recording_start")
                            ),
                            external_ref=external_ref,
                        )
                    )
                    response.created += 1

            next_page_token = payload.get("next_page_token") or ""
            if not next_page_token:
                break

    await db.commit()
    return response


@router.post("/zoom/sync", response_model=ZoomSyncResponse)
async def sync_zoom_calls(
    days: int = Query(30, ge=1, le=180),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import recent Zoom cloud recordings as call communication logs."""
    return await _run_zoom_sync(days=days, current_user=current_user, db=db)


@integrations_router.post("/sync-calls", response_model=ZoomSyncResponse)
async def sync_zoom_calls_compat(
    days: int = Query(30, ge=1, le=180),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compatibility endpoint for the integrations UI's Sync Calls button."""
    return await _run_zoom_sync(days=days, current_user=current_user, db=db)
