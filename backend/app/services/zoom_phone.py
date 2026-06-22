"""Zoom Phone call-history ingestion for the intake dashboard."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.communication_log import CommunicationLog
from app.models.tenant_credential import TenantCredential
from app.services.intake_archive_import import normalize_phone
from app.services.tenant_oauth_apps import get_zoom_phone_oauth_client
from app.services.token_vault import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)
settings = get_settings()

ZOOM_BASE = "https://api.zoom.us/v2"
ZOOM_PHONE_PROVIDER = "zoom_phone"


class ZoomPhoneIntegrationError(RuntimeError):
    """Raised when Zoom Phone credentials or APIs are unavailable."""


@dataclass(slots=True)
class ZoomPhoneImportResult:
    imported: int = 0
    updated: int = 0
    skipped: int = 0


def _fresh(expires_at: datetime | None) -> bool:
    if not expires_at:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < expires_at - timedelta(seconds=90)


def _expires_at(expires_in: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.debug("Could not parse Zoom Phone timestamp %r", value)
    return datetime.now(timezone.utc)


def _stringify(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


async def _get_or_create_credential(
    db: AsyncSession, tenant_id: str
) -> TenantCredential | None:
    result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.provider == ZOOM_PHONE_PROVIDER,
            TenantCredential.is_active,
        )
    )
    cred = result.scalar_one_or_none()
    if cred:
        return cred

    if not settings.ZOOM_PHONE_ACCOUNT_ID:
        return None

    cred = TenantCredential(
        tenant_id=tenant_id,
        provider=ZOOM_PHONE_PROVIDER,
        encrypted_access_token=encrypt_token("pending"),
        encrypted_refresh_token=None,
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        scopes=settings.ZOOM_PHONE_SCOPES,
        service_account_email=settings.ZOOM_PHONE_ACCOUNT_ID,
        is_active=True,
    )
    db.add(cred)
    await db.flush()
    return cred


async def get_zoom_phone_token(db: AsyncSession, tenant_id: str) -> str | None:
    """Return a tenant Zoom Phone token.

    Prefer a customer admin OAuth grant stored as ``zoom_phone``. The S2S/env
    path remains as an operator fallback while the portal grant rolls out.
    """
    cred = await _get_or_create_credential(db, tenant_id)
    if not cred:
        return None
    if _fresh(cred.token_expires_at):
        try:
            return decrypt_token(cred.encrypted_access_token)
        except Exception:
            logger.warning("Zoom Phone token decrypt failed; refreshing")

    if cred.encrypted_refresh_token:
        oauth_client = await get_zoom_phone_oauth_client(db, tenant_id=tenant_id)
        if not oauth_client:
            logger.warning("Zoom Phone OAuth refresh skipped; app credentials missing")
            return None
        refresh_token = decrypt_token(cred.encrypted_refresh_token)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://zoom.us/oauth/token",
                auth=(oauth_client.client_id, oauth_client.client_secret),
                data={
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if resp.status_code != 200:
            logger.warning(
                "Zoom Phone OAuth refresh failed: %s %s",
                resp.status_code,
                resp.text[:300],
            )
            return None

        data = resp.json()
        access_token = data.get("access_token")
        if not access_token:
            return None
        new_refresh_token = data.get("refresh_token")
        cred.encrypted_access_token = encrypt_token(access_token)
        if new_refresh_token:
            cred.encrypted_refresh_token = encrypt_token(new_refresh_token)
        cred.token_expires_at = _expires_at(int(data.get("expires_in") or 3600))
        cred.scopes = data.get("scope") or cred.scopes or settings.ZOOM_PHONE_SCOPES
        await db.flush()
        return access_token

    client_id = settings.ZOOM_PHONE_CLIENT_ID or settings.ZOOM_CLIENT_ID
    client_secret = settings.ZOOM_PHONE_CLIENT_SECRET or settings.ZOOM_CLIENT_SECRET
    account_id = cred.service_account_email or settings.ZOOM_PHONE_ACCOUNT_ID
    if not client_id or not client_secret or not account_id:
        return None

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://zoom.us/oauth/token",
            auth=(client_id, client_secret),
            params={
                "grant_type": "account_credentials",
                "account_id": account_id,
            },
        )
    if resp.status_code != 200:
        logger.warning(
            "Zoom Phone S2S token request failed: %s %s",
            resp.status_code,
            resp.text[:300],
        )
        return None

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        return None
    cred.encrypted_access_token = encrypt_token(access_token)
    cred.token_expires_at = _expires_at(int(data.get("expires_in") or 3600))
    cred.scopes = data.get("scope") or settings.ZOOM_PHONE_SCOPES
    cred.service_account_email = account_id
    await db.flush()
    return access_token


def _zoom_phone_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        code = body.get("code")
        message = body.get("message") or "Zoom Phone request failed."
        if code == 2031:
            return "Zoom Phone is not enabled for this Zoom account."
        if code == 104:
            return f"Zoom Phone token is missing required scopes: {message}"
        return f"{message} (Zoom code {code})" if code else message
    except Exception:
        return f"Zoom Phone request failed (HTTP {resp.status_code})."


async def probe_zoom_phone_connection(
    db: AsyncSession,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Probe the minimal Call History API used by intake sync."""
    token = await get_zoom_phone_token(db, tenant_id)
    if not token:
        raise ZoomPhoneIntegrationError("Zoom Phone OAuth is not connected.")

    today = datetime.now(timezone.utc).date().isoformat()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{ZOOM_BASE}/phone/call_history",
            headers={"Authorization": f"Bearer {token}"},
            params={"from": today, "to": today, "page_size": 1},
        )
    if resp.status_code != 200:
        raise ZoomPhoneIntegrationError(_zoom_phone_error(resp))
    data = resp.json()
    return {
        "ok": True,
        "sample_count": len(data.get("call_history") or data.get("call_logs") or []),
        "next_page_token": data.get("next_page_token") or "",
    }


def normalize_zoom_phone_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize Zoom Phone call-history/call-element payloads into log fields."""
    call_id = _stringify(
        _first(
            record,
            "call_element_id",
            "call_history_id",
            "call_history_uuid",
            "callHistoryUuid",
            "call_id",
            "id",
        )
    )
    if not call_id:
        return None

    direction = (_stringify(_first(record, "direction", "call_type")) or "inbound").lower()
    if direction not in {"inbound", "outbound"}:
        direction = "inbound" if "in" in direction else "outbound"

    caller_number = _stringify(
        _first(record, "caller_number", "caller_phone_number", "from_number", "from")
    )
    callee_number = _stringify(
        _first(record, "callee_number", "callee_phone_number", "to_number", "to")
    )
    caller_name = _stringify(_first(record, "caller_name", "caller_display_name", "from_name"))
    callee_name = _stringify(_first(record, "callee_name", "callee_display_name", "to_name"))

    phone = caller_number if direction == "inbound" else callee_number
    display_name = caller_name if direction == "inbound" else callee_name
    display_name = display_name or phone or "Unknown Zoom Phone caller"

    occurred_at = _parse_datetime(
        _first(record, "start_time", "date_time", "dateTime", "created_at", "end_time")
    )
    duration = _first(record, "duration", "duration_seconds", "call_duration")
    result = _stringify(_first(record, "result", "call_result", "disposition", "status"))
    recording_url = _stringify(
        _first(record, "recording_download_url", "recording_url", "download_url")
    )
    transcript_url = _stringify(
        _first(record, "transcript_download_url", "transcript_url")
    )
    summary = _stringify(_first(record, "summary", "call_summary", "ai_summary", "topic"))
    transcript = _stringify(_first(record, "transcript", "transcript_text", "body"))

    body_parts = [
        f"Zoom Phone {direction} call",
        f"Result: {result}" if result else None,
        f"Duration: {duration} seconds" if duration not in (None, "") else None,
        f"Recording: {recording_url}" if recording_url else None,
        f"Transcript: {transcript_url}" if transcript_url else None,
        transcript,
    ]

    return {
        "external_ref": f"zoom_phone:call:{call_id}",
        "direction": direction,
        "subject": f"Zoom Phone {direction} call: {display_name}",
        "summary": summary or result or f"Zoom Phone {direction} call",
        "body": "\n".join(part for part in body_parts if part),
        "occurred_at": occurred_at,
        "participants": {
            "provider": ZOOM_PHONE_PROVIDER,
            "call_id": call_id,
            "caller_name": caller_name,
            "callee_name": callee_name,
            "phone": phone,
            "caller_number": caller_number,
            "callee_number": callee_number,
            "normalized_phone": normalize_phone(phone),
            "direction": direction,
            "result": result,
            "duration_seconds": duration,
            "recording_url": recording_url,
            "transcript_url": transcript_url,
            "raw": record,
        },
    }


async def import_zoom_phone_records(
    db: AsyncSession,
    *,
    tenant_id: str,
    records: list[dict[str, Any]],
) -> ZoomPhoneImportResult:
    """Idempotently import Zoom Phone call-history records as CommunicationLogs."""
    result = ZoomPhoneImportResult()
    for record in records:
        normalized = normalize_zoom_phone_record(record)
        if not normalized:
            result.skipped += 1
            continue

        existing = (
            await db.execute(
                select(CommunicationLog).where(
                    CommunicationLog.tenant_id == tenant_id,
                    CommunicationLog.external_ref == normalized["external_ref"],
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.direction = normalized["direction"]
            existing.subject = normalized["subject"]
            existing.summary = normalized["summary"]
            existing.body = normalized["body"]
            existing.occurred_at = normalized["occurred_at"]
            existing.participants = normalized["participants"]
            result.updated += 1
            continue

        db.add(
            CommunicationLog(
                tenant_id=tenant_id,
                direction=normalized["direction"],
                channel="call",
                status="logged",
                subject=normalized["subject"],
                summary=normalized["summary"],
                body=normalized["body"],
                occurred_at=normalized["occurred_at"],
                external_ref=normalized["external_ref"],
                participants=normalized["participants"],
            )
        )
        result.imported += 1
    await db.flush()
    return result


async def fetch_zoom_phone_call_history(
    db: AsyncSession,
    *,
    tenant_id: str,
    days: int = 7,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """Fetch recent account call history from Zoom Phone."""
    token = await get_zoom_phone_token(db, tenant_id)
    if not token:
        raise ZoomPhoneIntegrationError("Zoom Phone OAuth is not connected.")

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 31)))
    path = "/phone/call_history"
    records: list[dict[str, Any]] = []
    next_page_token = ""

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"{ZOOM_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "from": since.date().isoformat(),
                    "to": datetime.now(timezone.utc).date().isoformat(),
                    "page_size": max(1, min(page_size, 300)),
                    **({"next_page_token": next_page_token} if next_page_token else {}),
                },
            )
            if resp.status_code != 200:
                raise ZoomPhoneIntegrationError(_zoom_phone_error(resp))
            data = resp.json()
            page_records = data.get("call_history") or data.get("call_logs") or data.get("calls") or []
            records.extend(page_records)
            next_page_token = data.get("next_page_token") or ""
            if not next_page_token:
                break
    return records


async def sync_zoom_phone_call_history(
    db: AsyncSession,
    *,
    tenant_id: str,
    days: int = 7,
) -> ZoomPhoneImportResult:
    records = await fetch_zoom_phone_call_history(db, tenant_id=tenant_id, days=days)
    return await import_zoom_phone_records(db, tenant_id=tenant_id, records=records)
