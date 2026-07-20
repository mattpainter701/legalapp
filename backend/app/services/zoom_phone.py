"""Zoom Phone call-history ingestion for the intake dashboard."""

from __future__ import annotations

import logging
import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker, set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.tenant_oauth_app import TenantOAuthApp
from app.services.intake_archive_import import normalize_phone
from app.services.tenant_oauth_apps import get_zoom_phone_oauth_client
from app.services.token_vault import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)
settings = get_settings()

ZOOM_BASE = "https://api.zoom.us/v2"
ZOOM_PHONE_PROVIDER = "zoom_phone"
ZOOM_PHONE_HISTORY_COMPLETED_EVENTS = {
    "phone.callee_call_history_completed",
    "phone.caller_call_history_completed",
    "phone.callee_call_element_completed",
    "phone.caller_call_element_completed",
}
_ZOOM_ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,255}$")


def _verified_zoom_account_binding(account_id: str | None) -> str | None:
    """Ignore numeric Account Numbers stored by the retired manual workflow."""

    normalized = str(account_id or "").strip()
    if (
        not normalized
        or normalized.isdecimal()
        or not _ZOOM_ACCOUNT_ID_PATTERN.fullmatch(normalized)
    ):
        return None
    return normalized


class ZoomPhoneIntegrationError(RuntimeError):
    """Raised when Zoom Phone credentials or APIs are unavailable."""


class ZoomPhoneReauthorizationRequired(ZoomPhoneIntegrationError):
    """Raised when an OAuth grant can no longer be refreshed safely."""


class ZoomPhonePermanentError(ZoomPhoneIntegrationError):
    """Raised when retrying the same provider request cannot succeed."""


@dataclass(slots=True)
class ZoomPhoneImportResult:
    imported: int = 0
    updated: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class ZoomPhoneWebhookJob:
    """Minimal, credential-free durable work derived from a signed webhook."""

    idempotency_key: str
    payload: dict[str, str | None]


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


def _path_value(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current in (None, ""):
            return None
    return current


def _first_path(data: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value = _path_value(data, path) if "." in path else data.get(path)
        if value not in (None, ""):
            return value
    return None


def _first_phone(data: dict[str, Any], *paths: str) -> str | None:
    for path in paths:
        value = _stringify(_first_path(data, path))
        if not value:
            continue
        if normalize_phone(value):
            return value
    return None


def _normalize_zoom_direction(record: dict[str, Any]) -> str | None:
    raw = _stringify(
        _first_path(
            record,
            "direction",
            "call_type",
            "call.direction",
            "details.direction",
        )
    )
    if not raw:
        return None
    direction = raw.lower().replace("-", "_").strip()
    if direction in {"inbound", "incoming", "in"}:
        return "inbound"
    if direction in {"outbound", "outgoing", "out"}:
        return "outbound"
    if "inbound" in direction or "incoming" in direction:
        return "inbound"
    if "outbound" in direction or "outgoing" in direction:
        return "outbound"
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


def zoom_webhook_validation_response(plain_token: str, secret: str) -> dict[str, str]:
    """Build Zoom endpoint.url_validation response."""
    encrypted = hmac.new(
        secret.encode("utf-8"),
        plain_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"plainToken": plain_token, "encryptedToken": encrypted}


def verify_zoom_webhook_signature(
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    secret: str | None = None,
    tolerance_seconds: int = 300,
) -> bool:
    """Validate Zoom webhook x-zm-signature."""
    if not secret or not timestamp or not signature:
        return False
    if tolerance_seconds > 0:
        try:
            sent_at = int(timestamp)
        except (TypeError, ValueError):
            return False
        now = int(datetime.now(timezone.utc).timestamp())
        if abs(now - sent_at) > tolerance_seconds:
            return False
    expected = (
        "v0="
        + hmac.new(
            secret.encode("utf-8"),
            b"v0:" + timestamp.encode("utf-8") + b":" + body,
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def extract_zoom_phone_webhook_call_logs(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Return inbound completed call-history logs from a Zoom Phone webhook."""
    event_name = _stringify(event.get("event"))
    if event_name not in ZOOM_PHONE_HISTORY_COMPLETED_EVENTS:
        return []
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return []
    obj = payload.get("object") or {}
    if not isinstance(obj, dict):
        return []
    logs = (
        obj.get("call_elements")
        or obj.get("call_logs")
        or obj.get("call_history")
        or []
    )
    if not isinstance(logs, list):
        return []
    return [
        log
        for log in logs
        if isinstance(log, dict) and _normalize_zoom_direction(log) in {None, "inbound"}
    ]


def zoom_phone_webhook_jobs(event: dict[str, Any]) -> list[ZoomPhoneWebhookJob]:
    """Create one stable, minimal job per provider call element/history item.

    A v3 element ID is the durable work identity so every transfer leg is
    processed. The history UUID remains the business/canonical communication
    identity so all legs converge on one intake call. V2 has no element ID and
    falls back to its history ID. The work ID is hashed before queue storage;
    the payload retains only IDs needed for the authoritative detail read.
    """
    event_name = _stringify(event.get("event"))
    if event_name not in ZOOM_PHONE_HISTORY_COMPLETED_EVENTS:
        return []

    jobs: list[ZoomPhoneWebhookJob] = []
    seen: set[str] = set()
    for call_log in extract_zoom_phone_webhook_call_logs(event):
        history_id = _stringify(
            _first(
                call_log,
                "call_history_id",
                "call_history_uuid",
                "callHistoryUuid",
                "id",
                "callLogId",
            )
        )
        element_id = _stringify(_first(call_log, "call_element_id"))
        if "call_element_completed" in event_name and (
            not element_id or not history_id
        ):
            # A v3 element without its history UUID cannot safely converge with
            # later reconciliation. Ignore the malformed item; the hourly
            # call-history reconciliation remains the recovery source.
            continue
        stable_call_id = history_id or element_id
        if not stable_call_id:
            continue
        work_id = element_id or history_id
        digest = hashlib.sha256(
            f"zoom_phone_call_element:{work_id}".encode("utf-8")
        ).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        jobs.append(
            ZoomPhoneWebhookJob(
                idempotency_key=digest,
                payload={
                    "event_name": event_name,
                    "call_history_id": history_id,
                    "call_element_id": element_id,
                    "stable_call_id": stable_call_id,
                },
            )
        )
    return jobs


async def _get_credential(db: AsyncSession, tenant_id: str) -> TenantCredential | None:
    result = await db.execute(
        select(TenantCredential)
        .where(
            TenantCredential.tenant_id == uuid.UUID(str(tenant_id)),
            TenantCredential.provider == ZOOM_PHONE_PROVIDER,
            TenantCredential.is_active,
        )
        .with_for_update()
    )
    cred = result.scalar_one_or_none()
    return cred


async def _get_zoom_phone_token(
    db: AsyncSession,
    tenant_id: str,
    *,
    force_refresh: bool = False,
    rejected_access_token: str | None = None,
) -> str | None:
    """Return a tenant Zoom Phone token.

    Refreshes run in a dedicated, tenant-scoped transaction and lock the grant
    row.  A successful rotating refresh token is committed *before* any caller
    performs a downstream Zoom read, so a later detail/sync failure cannot roll
    the credential back to an invalidated token.  ``db`` is retained in the
    public signature for existing callers; no caller-owned transaction is
    committed here.
    """
    del db
    tenant_uuid = uuid.UUID(str(tenant_id))
    async with async_session_maker() as token_db:
        await set_tenant_context(token_db, str(tenant_uuid))
        # Bound concurrent refreshers. After the first committer releases the
        # row, the next waiter re-checks freshness and reuses the new token.
        await token_db.execute(text("SET LOCAL lock_timeout = '10s'"))
        tenant_active = await token_db.scalar(
            select(Tenant.is_active).where(Tenant.id == tenant_uuid)
        )
        if not tenant_active:
            raise ZoomPhoneIntegrationError("Zoom Phone tenant is inactive.")

        cred = await _get_credential(token_db, str(tenant_uuid))
        if not cred:
            return None
        try:
            oauth_client = await get_zoom_phone_oauth_client(
                token_db, tenant_id=tenant_uuid
            )
        except Exception:
            oauth_client = None
        if not oauth_client:
            cred.health = "reauthorization_required"
            cred.is_active = False
            cred.last_refresh_error = "Zoom OAuth app credentials are unavailable."
            await token_db.commit()
            raise ZoomPhoneReauthorizationRequired(
                "Zoom Phone OAuth app credentials must be restored or reconnected."
            )
        # API connectivity is independent from real-time webhook account
        # binding. Normalize the retired blocking state while preserving the
        # refreshable grant and every token.
        legacy_verification_health = cred.health == "account_verification_required"
        if legacy_verification_health:
            cred.health = "healthy"
            if (
                not cred.last_refresh_error
                or "webhook" in cred.last_refresh_error.lower()
                or "account id" in cred.last_refresh_error.lower()
            ):
                cred.last_refresh_error = None
        current_access_token: str | None = None
        if cred.encrypted_access_token:
            try:
                current_access_token = decrypt_token(cred.encrypted_access_token)
            except Exception:
                logger.warning("Zoom Phone token decrypt failed; refreshing")
        if _fresh(cred.token_expires_at) and current_access_token:
            if not force_refresh:
                if legacy_verification_health:
                    await token_db.commit()
                return current_access_token
            # Two requests may both receive a 401 for the same cached token.
            # Once the first waiter rotates it, the second waiter must reuse the
            # new committed token instead of rotating the fresh grant again.
            if rejected_access_token and not hmac.compare_digest(
                current_access_token, rejected_access_token
            ):
                if legacy_verification_health:
                    await token_db.commit()
                return current_access_token

        if cred.encrypted_refresh_token:
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
                try:
                    error_body = resp.json()
                except Exception:
                    error_body = {}
                error_code = str(
                    error_body.get("error") or error_body.get("code") or ""
                ).lower()
                error_reason = str(
                    error_body.get("reason") or error_body.get("message") or ""
                ).lower()
                oauth_error = f"{error_code} {error_reason}".replace("_", " ")
                requires_reauth = error_code in {
                    "invalid_grant",
                    "invalid_client",
                    "unauthorized_client",
                    "4702",
                    "4704",
                    "4706",
                    "4711",
                } or any(
                    marker in oauth_error
                    for marker in (
                        "invalid token",
                        "invalid refresh",
                        "refresh token",
                        "revoked",
                        "invalid client",
                        "unauthorized client",
                        "client secret",
                        "client credential",
                    )
                )
                cred.health = (
                    "reauthorization_required" if requires_reauth else "degraded"
                )
                if requires_reauth:
                    cred.is_active = False
                cred.last_refresh_error = (
                    "Zoom OAuth grant requires reauthorization."
                    if requires_reauth
                    else f"Zoom OAuth refresh failed (HTTP {resp.status_code})."
                )
                await token_db.commit()
                if requires_reauth:
                    raise ZoomPhoneReauthorizationRequired(
                        "Zoom Phone OAuth authorization expired or was revoked."
                    )
                raise ZoomPhoneIntegrationError(
                    f"Zoom Phone OAuth refresh failed (HTTP {resp.status_code})."
                )

            data = resp.json()
            access_token = data.get("access_token")
            if not access_token:
                cred.health = "degraded"
                cred.last_refresh_error = "Zoom OAuth response had no access token."
                await token_db.commit()
                raise ZoomPhoneIntegrationError(
                    "Zoom Phone OAuth refresh returned no access token."
                )
            returned_account_id = _verified_zoom_account_binding(data.get("account_id"))
            existing_binding = _verified_zoom_account_binding(oauth_client.account_id)
            if (
                returned_account_id
                and existing_binding
                and not hmac.compare_digest(returned_account_id, existing_binding)
            ):
                cred.health = "reauthorization_required"
                cred.is_active = False
                cred.last_refresh_error = (
                    "Zoom OAuth refresh returned a different account mapping."
                )
                await token_db.commit()
                raise ZoomPhoneReauthorizationRequired(
                    "Zoom Phone OAuth account mapping changed."
                )
            new_refresh_token = data.get("refresh_token")
            cred.encrypted_access_token = encrypt_token(access_token)
            if new_refresh_token:
                cred.encrypted_refresh_token = encrypt_token(new_refresh_token)
            cred.token_expires_at = _expires_at(int(data.get("expires_in") or 3600))
            cred.scopes = data.get("scope") or cred.scopes or settings.ZOOM_PHONE_SCOPES
            if returned_account_id and not existing_binding:
                app = await token_db.scalar(
                    select(TenantOAuthApp)
                    .where(
                        TenantOAuthApp.tenant_id == tenant_uuid,
                        TenantOAuthApp.provider == ZOOM_PHONE_PROVIDER,
                        TenantOAuthApp.is_active,
                    )
                    .with_for_update()
                )
                if app:
                    app.zoom_account_id = returned_account_id
            if returned_account_id:
                # Keep the independently persisted grant-side binding in sync;
                # status is verified only when both provider-derived values
                # exist and agree.
                cred.service_account_email = returned_account_id
            cred.health = "healthy"
            cred.last_refresh_at = datetime.now(timezone.utc)
            cred.last_refresh_error = None
            await token_db.commit()
            return access_token

        cred.health = "reauthorization_required"
        cred.is_active = False
        cred.last_refresh_error = "Zoom OAuth refresh token is unavailable."
        await token_db.commit()
        raise ZoomPhoneReauthorizationRequired(
            "Zoom Phone OAuth is not connected with a refreshable tenant grant."
        )


async def get_zoom_phone_token(
    db: AsyncSession,
    tenant_id: str,
    *,
    force_refresh: bool = False,
    rejected_access_token: str | None = None,
) -> str | None:
    """Return a token for the tenant's refreshable Zoom Phone API grant."""
    return await _get_zoom_phone_token(
        db,
        tenant_id,
        force_refresh=force_refresh,
        rejected_access_token=rejected_access_token,
    )


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


def _zoom_phone_response_exception(
    resp: httpx.Response, *, not_found_retryable: bool = False
) -> ZoomPhoneIntegrationError:
    message = _zoom_phone_error(resp)
    if (
        resp.status_code == 429
        or resp.status_code >= 500
        or (not_found_retryable and resp.status_code == 404)
    ):
        return ZoomPhoneIntegrationError(message)
    return ZoomPhonePermanentError(message)


def _zoom_error_code(resp: httpx.Response) -> int | None:
    try:
        return int(resp.json().get("code"))
    except (AttributeError, TypeError, ValueError):
        return None


async def _mark_zoom_phone_grant(
    tenant_id: str, *, health: str, error: str | None, deactivate: bool
) -> None:
    tenant_uuid = uuid.UUID(str(tenant_id))
    async with async_session_maker() as state_db:
        await set_tenant_context(state_db, str(tenant_uuid))
        credential = await state_db.scalar(
            select(TenantCredential)
            .where(
                TenantCredential.tenant_id == tenant_uuid,
                TenantCredential.provider == ZOOM_PHONE_PROVIDER,
            )
            .with_for_update()
        )
        if credential:
            credential.health = health
            credential.last_refresh_error = error
            if deactivate:
                credential.is_active = False
            await state_db.commit()


async def _zoom_phone_get(
    db: AsyncSession,
    *,
    tenant_id: str,
    url: str,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """GET once, forcing one persisted refresh on a revoked cached token."""
    token = await _get_zoom_phone_token(
        db,
        tenant_id,
    )
    if not token:
        raise ZoomPhoneReauthorizationRequired("Zoom Phone OAuth is not connected.")

    async def request(access_token: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=20) as client:
            return await client.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )

    response = await request(token)
    if response.status_code == 401 or _zoom_error_code(response) == 124:
        refreshed = await _get_zoom_phone_token(
            db,
            tenant_id,
            force_refresh=True,
            rejected_access_token=token,
        )
        if not refreshed:
            raise ZoomPhoneReauthorizationRequired(
                "Zoom Phone OAuth could not refresh a rejected access token."
            )
        response = await request(refreshed)
        if response.status_code == 401 or _zoom_error_code(response) == 124:
            await _mark_zoom_phone_grant(
                tenant_id,
                health="reauthorization_required",
                error="Zoom rejected the refreshed access token.",
                deactivate=True,
            )
            raise ZoomPhoneReauthorizationRequired(
                "Zoom Phone rejected the refreshed OAuth grant."
            )
    if _zoom_error_code(response) == 104:
        await _mark_zoom_phone_grant(
            tenant_id,
            health="missing_scopes",
            error="Zoom rejected the required Phone API scope.",
            deactivate=False,
        )
    return response


async def probe_zoom_phone_connection(
    db: AsyncSession,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Probe the minimal Call History API used by intake sync."""
    today = datetime.now(timezone.utc).date().isoformat()
    resp = await _zoom_phone_get(
        db,
        tenant_id=tenant_id,
        url=f"{ZOOM_BASE}/phone/call_history",
        params={"from": today, "to": today, "page_size": 1},
    )
    if resp.status_code != 200:
        if _zoom_error_code(resp) != 104:
            await _mark_zoom_phone_grant(
                tenant_id,
                health="degraded",
                error=_zoom_phone_error(resp),
                deactivate=False,
            )
        raise _zoom_phone_response_exception(resp)
    data = resp.json()
    await _mark_zoom_phone_grant(
        tenant_id,
        health="healthy",
        error=None,
        deactivate=False,
    )
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
            "canonical_call_id",
            "call_history_id",
            "call_history_uuid",
            "callHistoryUuid",
            "call_element_id",
            "call_id",
            "id",
        )
    )
    if not call_id:
        return None

    direction = _normalize_zoom_direction(record)
    if direction != "inbound":
        return None

    caller_number = _first_phone(
        record,
        "caller_number",
        "caller_phone_number",
        "caller_did_number",
        "caller.phone_number",
        "caller.number",
        "caller.did_number",
        "caller.extension_number",
        "from_number",
        "from.phone_number",
        "from.number",
        "from",
    )
    callee_number = _first_phone(
        record,
        "callee_number",
        "callee_phone_number",
        "callee_did_number",
        "callee.phone_number",
        "callee.number",
        "callee.did_number",
        "callee.extension_number",
        "to_number",
        "to.phone_number",
        "to.number",
        "to",
    )
    caller_name = _stringify(
        _first_path(
            record,
            "caller_name",
            "caller_display_name",
            "caller.name",
            "caller.display_name",
            "caller.user_name",
            "from_name",
            "from.name",
        )
    )
    callee_name = _stringify(
        _first_path(
            record,
            "callee_name",
            "callee_display_name",
            "callee.name",
            "callee.display_name",
            "callee.user_name",
            "to_name",
            "to.name",
        )
    )
    caller_identity = _stringify(
        _first_path(
            record,
            "caller_number",
            "caller_phone_number",
            "from_number",
            "from",
        )
    )
    if not caller_name and caller_identity and not normalize_phone(caller_identity):
        caller_name = caller_identity

    phone = caller_number
    normalized_phone = normalize_phone(phone)
    display_name = caller_name
    display_name = display_name or phone or "Unknown Zoom Phone caller"

    occurred_at = _parse_datetime(
        _first(record, "start_time", "date_time", "dateTime", "created_at", "end_time")
    )
    duration = _first(record, "duration", "duration_seconds", "call_duration")
    result = _stringify(
        _first(record, "result", "call_result", "disposition", "status")
    )
    recording_url = _stringify(
        _first(record, "recording_download_url", "recording_url", "download_url")
    )
    transcript_url = _stringify(
        _first(record, "transcript_download_url", "transcript_url")
    )
    summary = _stringify(
        _first(record, "summary", "call_summary", "ai_summary", "topic")
    )
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
            "normalized_phone": normalized_phone,
            "direction": direction,
            "result": result,
            "duration_seconds": duration,
            "call_summary": summary,
            "transcript_text": transcript,
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

        values = {
            "tenant_id": uuid.UUID(str(tenant_id)),
            "direction": normalized["direction"],
            "channel": "call",
            "status": "logged",
            "subject": normalized["subject"],
            "summary": normalized["summary"],
            "body": normalized["body"],
            "occurred_at": normalized["occurred_at"],
            "external_ref": normalized["external_ref"],
            "participants": normalized["participants"],
        }
        inserted_id = await db.scalar(
            pg_insert(CommunicationLog)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    CommunicationLog.tenant_id,
                    CommunicationLog.external_ref,
                ],
                index_where=text("external_ref LIKE 'zoom_phone:call:%'"),
            )
            .returning(CommunicationLog.id)
        )
        if inserted_id:
            result.imported += 1
            continue

        existing = await db.scalar(
            select(CommunicationLog)
            .where(
                CommunicationLog.tenant_id == uuid.UUID(str(tenant_id)),
                CommunicationLog.external_ref == normalized["external_ref"],
            )
            .with_for_update()
        )
        if existing is None:
            raise ZoomPhoneIntegrationError(
                "Zoom Phone call upsert lost its canonical row."
            )
        captured_by_staff = bool(existing.created_by_user_id or existing.contact_id)
        if captured_by_staff:
            # Intake staff own the curated narrative, corrected caller identity,
            # contact link and task workflow. Reconciliation may refresh only
            # provider-owned metadata after capture.
            merged_participants = dict(existing.participants or {})
            provider_participants = normalized["participants"]
            for key in {
                "provider",
                "call_id",
                "callee_name",
                "caller_number",
                "callee_number",
                "direction",
                "result",
                "duration_seconds",
                "call_summary",
                "transcript_text",
                "recording_url",
                "transcript_url",
                "raw",
                "webhook_call_history_id",
                "webhook_call_element_id",
                "webhook_event",
            }:
                if key in provider_participants:
                    merged_participants[key] = provider_participants[key]
            if merged_participants == (existing.participants or {}):
                result.skipped += 1
                continue
            existing.participants = merged_participants
        else:
            unchanged = (
                existing.direction == normalized["direction"]
                and existing.subject == normalized["subject"]
                and existing.summary == normalized["summary"]
                and existing.body == normalized["body"]
                and existing.occurred_at == normalized["occurred_at"]
                and (existing.participants or {}) == normalized["participants"]
            )
            if unchanged:
                result.skipped += 1
                continue
            existing.direction = normalized["direction"]
            existing.subject = normalized["subject"]
            existing.summary = normalized["summary"]
            existing.body = normalized["body"]
            existing.occurred_at = normalized["occurred_at"]
            existing.participants = normalized["participants"]
        existing.updated_at = datetime.now(timezone.utc)
        result.updated += 1
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
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 31)))
    path = "/phone/call_history"
    records: list[dict[str, Any]] = []
    next_page_token = ""
    seen_page_tokens: set[str] = set()
    page_count = 0

    while True:
        page_count += 1
        resp = await _zoom_phone_get(
            db,
            tenant_id=tenant_id,
            url=f"{ZOOM_BASE}{path}",
            params={
                "from": since.date().isoformat(),
                "to": datetime.now(timezone.utc).date().isoformat(),
                "directions": "inbound",
                "page_size": max(1, min(page_size, 300)),
                **({"next_page_token": next_page_token} if next_page_token else {}),
            },
        )
        if resp.status_code != 200:
            raise _zoom_phone_response_exception(resp)
        data = resp.json()
        page_records = (
            data.get("call_history") or data.get("call_logs") or data.get("calls") or []
        )
        records.extend(page_records)
        next_page_token = data.get("next_page_token") or ""
        if not next_page_token:
            break
        if next_page_token in seen_page_tokens:
            raise ZoomPhoneIntegrationError(
                "Zoom Phone repeated a call-history page token."
            )
        seen_page_tokens.add(next_page_token)
        if page_count >= 100:
            raise ZoomPhoneIntegrationError(
                "Zoom Phone call-history pagination exceeded the safe page limit."
            )
    return records


async def fetch_zoom_phone_call_history_detail(
    db: AsyncSession,
    *,
    tenant_id: str,
    call_history_id: str | None = None,
    call_element_id: str | None = None,
) -> dict[str, Any]:
    """Fetch authoritative Zoom Phone detail for a v2 history or v3 element id."""
    if not call_history_id and not call_element_id:
        raise ZoomPhoneIntegrationError("Zoom Phone call identifier was missing.")

    if call_element_id:
        resp = await _zoom_phone_get(
            db,
            tenant_id=tenant_id,
            url=f"{ZOOM_BASE}/phone/call_element/{call_element_id}",
        )
    else:
        resp = await _zoom_phone_get(
            db,
            tenant_id=tenant_id,
            url=f"{ZOOM_BASE}/phone/call_history_detail/{call_history_id}",
        )
        if resp.status_code == 404:
            resp = await _zoom_phone_get(
                db,
                tenant_id=tenant_id,
                url=f"{ZOOM_BASE}/phone/call_history/{call_history_id}",
            )
    if resp.status_code != 200:
        raise _zoom_phone_response_exception(resp, not_found_retryable=True)
    data = resp.json()
    if not isinstance(data, dict):
        raise ZoomPhoneIntegrationError("Zoom Phone call detail response was invalid.")
    return data


async def _promote_verified_zoom_account(
    db: AsyncSession,
    *,
    tenant_id: str,
    account_id: str,
) -> None:
    tenant_uuid = uuid.UUID(str(tenant_id))
    app = await db.scalar(
        select(TenantOAuthApp)
        .where(
            TenantOAuthApp.tenant_id == tenant_uuid,
            TenantOAuthApp.provider == ZOOM_PHONE_PROVIDER,
            TenantOAuthApp.is_active,
        )
        .with_for_update()
    )
    credential = await db.scalar(
        select(TenantCredential)
        .where(
            TenantCredential.tenant_id == tenant_uuid,
            TenantCredential.provider == ZOOM_PHONE_PROVIDER,
            TenantCredential.is_active,
        )
        .with_for_update()
    )
    provider_account_id = _verified_zoom_account_binding(account_id)
    app_binding = _verified_zoom_account_binding(app.zoom_account_id if app else None)
    credential_binding = _verified_zoom_account_binding(
        credential.service_account_email if credential else None
    )
    if (
        not app
        or not credential
        or not provider_account_id
        or credential.health
        not in {
            "healthy",
            "account_verification_required",
        }
    ):
        raise ZoomPhoneReauthorizationRequired(
            "Zoom Phone account mapping changed during verification."
        )
    if app_binding and not hmac.compare_digest(app_binding, provider_account_id):
        raise ZoomPhonePermanentError("Zoom webhook account binding already exists.")
    if credential_binding and not hmac.compare_digest(
        credential_binding, provider_account_id
    ):
        raise ZoomPhonePermanentError(
            "Zoom OAuth grant account binding already exists."
        )
    app.zoom_account_id = provider_account_id
    credential.service_account_email = provider_account_id
    credential.health = "healthy"
    credential.last_refresh_error = None


async def import_zoom_phone_webhook_job(
    db: AsyncSession,
    *,
    tenant_id: str,
    payload: dict[str, Any],
) -> ZoomPhoneImportResult:
    """Resolve and import one credential-free durable webhook payload."""
    event_name = _stringify(payload.get("event_name"))
    history_id = _stringify(payload.get("call_history_id"))
    element_id = _stringify(payload.get("call_element_id"))
    stable_call_id = _stringify(payload.get("stable_call_id"))
    if event_name not in ZOOM_PHONE_HISTORY_COMPLETED_EVENTS:
        raise ValueError("Unsupported Zoom Phone durable event type.")
    if not stable_call_id or not (history_id or element_id):
        raise ValueError("Zoom Phone durable event is missing a provider call ID.")

    account_binding = payload.get("account_binding")
    if account_binding is not None:
        if (
            not isinstance(account_binding, dict)
            or account_binding.get("proof") != "signed_event_exact_call_fetch"
            or not _stringify(account_binding.get("account_id"))
        ):
            raise ValueError("Invalid Zoom Phone account-binding proof.")
        binding_account_id = str(account_binding["account_id"])
        detail = await fetch_zoom_phone_call_history_detail(
            db,
            tenant_id=tenant_id,
            call_history_id=history_id,
            call_element_id=element_id,
        )
        returned_element_id = _stringify(
            _first_path(detail, "call_element_id", "call_element.call_element_id")
        )
        returned_history_id = _stringify(
            _first_path(
                detail,
                "call_history_id",
                "call_history_uuid",
                "call_history.call_history_id",
                "call_history.call_history_uuid",
            )
        )
        if (
            element_id
            and returned_element_id
            and not hmac.compare_digest(element_id, returned_element_id)
        ):
            raise ZoomPhonePermanentError(
                "Zoom Phone returned a different call element during account binding."
            )
        if (
            history_id
            and returned_history_id
            and not hmac.compare_digest(history_id, returned_history_id)
        ):
            raise ZoomPhonePermanentError(
                "Zoom Phone returned a different call history during account binding."
            )
        await _promote_verified_zoom_account(
            db,
            tenant_id=tenant_id,
            account_id=binding_account_id,
        )
    else:
        detail = await fetch_zoom_phone_call_history_detail(
            db,
            tenant_id=tenant_id,
            call_history_id=history_id,
            call_element_id=element_id,
        )
    record = {
        **detail,
        # Stabilize the communication idempotency key across caller/callee and
        # v2/v3 event variants even when detail includes a leg/element ID.
        "canonical_call_id": stable_call_id,
        "webhook_call_history_id": history_id,
        "webhook_call_element_id": element_id,
        "webhook_event": event_name,
    }
    return await import_zoom_phone_records(
        db,
        tenant_id=tenant_id,
        records=[record],
    )


async def sync_zoom_phone_call_history(
    db: AsyncSession,
    *,
    tenant_id: str,
    days: int = 7,
) -> ZoomPhoneImportResult:
    records = await fetch_zoom_phone_call_history(db, tenant_id=tenant_id, days=days)
    return await import_zoom_phone_records(db, tenant_id=tenant_id, records=records)
