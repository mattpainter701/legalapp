"""Microsoft Teams Phone (voice) call capture.

Teams voice is the Teams-side counterpart of the Zoom Phone integration: it
lands inbound calls in ``communication_logs`` as ``channel="call"`` rows so the
intake dashboard treats a Teams Phone call exactly like a Zoom Phone call.

Two things make it structurally different from ``app.services.teams``:

* **Auth.** Graph exposes call records only through *application* permissions
  (``CallRecords.Read.All``). There is no delegated equivalent, so this module
  runs an app-only client-credentials grant against the customer's Entra
  directory rather than reusing the delegated Teams token.
* **Delivery.** There are two feeds, and both are needed:
    - ``/communications/callRecords`` change notifications for low latency.
      Graph posts a bare call-record id; the authoritative read is ours.
    - ``getPstnCalls`` for reconciliation. The PSTN usage report lags real time
      (Microsoft publishes it with a delay), so it is the backstop that heals
      missed or dropped notifications, not the primary feed.

``teams_voice:call:<callRecordId>`` is the idempotency key, enforced by a
partial unique index, so both feeds converge on one communication log.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.communication_log import CommunicationLog
from app.models.teams_voice_setting import TeamsVoiceSetting
from app.services.intake_archive_import import normalize_phone
from app.services.tenant_oauth_apps import get_tenant_oauth_app
from app.services.token_vault import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)
settings = get_settings()

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TEAMS_VOICE_PROVIDER = "teams_voice"
TEAMS_VOICE_APP_PROVIDER = "teams_voice"

# The single application role voice capture needs. Surfaced to admins verbatim
# so the Entra consent screen and our setup instructions cannot drift.
TEAMS_VOICE_APP_ROLE = "CallRecords.Read.All"
TEAMS_VOICE_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

# Graph caps callRecords subscriptions at 4230 minutes. Ask for a shorter
# window and renew well before expiry so a late scheduler tick cannot silently
# drop the feed.
SUBSCRIPTION_MINUTES = 4230
SUBSCRIPTION_RENEW_BEFORE = timedelta(hours=12)

_ENTRA_TENANT_MIN_LEN = 8
_ENTRA_TENANT_MAX_LEN = 64
_PAGE_LIMIT = 50


class TeamsVoiceError(RuntimeError):
    """Raised when Teams voice configuration or Graph access is unusable."""


class TeamsVoiceNotConfigured(TeamsVoiceError):
    """Raised when the tenant has not enabled/configured Teams voice."""


class TeamsVoicePermanentError(TeamsVoiceError):
    """Raised when retrying the same Graph request cannot succeed."""


@dataclass(slots=True)
class TeamsVoiceImportResult:
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    # Newly captured calls, for callers that want to announce them (e.g. the
    # Teams channel notification). Populated on insert only, so a
    # reconciliation pass that merely refreshes provider metadata stays quiet.
    captured: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TeamsVoiceWebhookJob:
    """Minimal, credential-free durable work derived from a notification."""

    idempotency_key: str
    payload: dict[str, str | None]


@dataclass(slots=True)
class TeamsVoiceAppCredentials:
    client_id: str
    client_secret: str
    source: str


# ── Small helpers ────────────────────────────────────────────────────────


def _stringify(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


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
            logger.debug("Could not parse Teams voice timestamp %r", value)
    return datetime.now(timezone.utc)


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _parse_datetime(value)


def parse_iso_duration_seconds(value: Any) -> int | None:
    """Parse the ISO-8601 durations Graph reports (e.g. ``PT2M13S``).

    Graph returns call length as an ISO duration on call records and as plain
    seconds on the PSTN report, so both shapes reach the normalizer.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    raw = str(value).strip()
    if raw.isdecimal():
        return int(raw)
    if not raw.upper().startswith("PT"):
        return None
    total = 0.0
    number = ""
    for char in raw[2:].upper():
        if char.isdigit() or char == ".":
            number += char
            continue
        if not number:
            return None
        try:
            magnitude = float(number)
        except ValueError:
            return None
        if char == "H":
            total += magnitude * 3600
        elif char == "M":
            total += magnitude * 60
        elif char == "S":
            total += magnitude
        else:
            return None
        number = ""
    if number:
        return None
    return int(total)


def valid_entra_tenant_id(value: str | None) -> str | None:
    """Accept a directory GUID or verified domain; reject ``common``.

    The multi-tenant ``common`` / ``organizations`` endpoints cannot issue an
    app-only token, so storing one would produce a configuration that looks
    saved but can never authenticate.
    """
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"common", "organizations", "consumers"}:
        return None
    if not (_ENTRA_TENANT_MIN_LEN <= len(normalized) <= _ENTRA_TENANT_MAX_LEN):
        return None
    if not all(c.isalnum() or c in "-." for c in normalized):
        return None
    return normalized


def _pstn_direction(record: dict[str, Any]) -> str | None:
    """Classify a ``pstnCallLogRow`` by its ``callType``.

    Graph spells these ``ByotIn``/``UserIn``/``ConfIn`` for inbound and
    ``...Out`` for outbound; anything unrecognized is left unclassified rather
    than guessed at.
    """
    raw = _stringify(_first(record, "callType", "call_type", "direction"))
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in {"inbound", "incoming", "in"} or lowered.endswith("in"):
        return "inbound"
    if lowered in {"outbound", "outgoing", "out"} or lowered.endswith("out"):
        return "outbound"
    return None


def _endpoint_identity(endpoint: Any) -> tuple[str | None, str | None]:
    """Pull ``(phone_number, display_name)`` out of a call-record endpoint."""
    if not isinstance(endpoint, dict):
        return None, None
    identity = endpoint.get("identity")
    if not isinstance(identity, dict):
        identity = {}
    phone = identity.get("phone") if isinstance(identity.get("phone"), dict) else {}
    user = identity.get("user") if isinstance(identity.get("user"), dict) else {}
    number = _stringify(_first(phone, "id", "displayName"))
    name = _stringify(
        _first(user, "displayName") or _first(phone, "displayName")
    ) or _stringify(endpoint.get("name"))
    return number, name


def _call_record_parties(record: dict[str, Any]) -> tuple[dict, dict]:
    """Best-effort caller/callee extraction from a Graph ``callRecord``.

    Prefers the first session's caller/callee endpoints (where PSTN numbers
    live) and falls back to the organizer plus first participant for records
    fetched without ``$expand=sessions``.
    """
    caller: dict[str, str | None] = {"number": None, "name": None}
    callee: dict[str, str | None] = {"number": None, "name": None}

    sessions = record.get("sessions")
    if isinstance(sessions, list):
        for session in sessions:
            if not isinstance(session, dict):
                continue
            caller_number, caller_name = _endpoint_identity(session.get("caller"))
            callee_number, callee_name = _endpoint_identity(session.get("callee"))
            caller["number"] = caller["number"] or caller_number
            caller["name"] = caller["name"] or caller_name
            callee["number"] = callee["number"] or callee_number
            callee["name"] = callee["name"] or callee_name
            if caller["number"] and callee["number"]:
                break

    if not caller["name"] and not caller["number"]:
        _, organizer_name = _endpoint_identity(record.get("organizer"))
        caller["name"] = organizer_name

    participants = record.get("participants")
    if isinstance(participants, list) and not callee["name"] and not callee["number"]:
        for participant in participants:
            number, name = _endpoint_identity(participant)
            if number or name:
                callee["number"] = callee["number"] or number
                callee["name"] = callee["name"] or name
                break

    return caller, callee


# ── Normalization / import ───────────────────────────────────────────────


def normalize_teams_voice_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a Graph call record or PSTN report row into log fields.

    Returns ``None`` for anything that is not an inbound call — outbound and
    internal Teams traffic is out of scope for intake capture, matching the
    Zoom Phone behavior.
    """
    call_id = _stringify(
        _first(
            record,
            "canonical_call_id",
            "callRecordId",
            "call_record_id",
            "id",
            "callId",
            "call_id",
        )
    )
    if not call_id:
        return None

    caller, callee = _call_record_parties(record)

    direction = _pstn_direction(record)
    if direction is None:
        # A raw callRecord carries no callType. Infer from the endpoints: a
        # PSTN number on the caller side and none on the callee side is an
        # inbound call into the firm.
        caller_is_pstn = bool(normalize_phone(caller["number"] or ""))
        callee_is_pstn = bool(normalize_phone(callee["number"] or ""))
        if caller_is_pstn and not callee_is_pstn:
            direction = "inbound"
        elif callee_is_pstn and not caller_is_pstn:
            direction = "outbound"
    if direction != "inbound":
        return None

    caller_number = _stringify(
        _first(record, "callerNumber", "caller_number") or caller["number"]
    )
    callee_number = _stringify(
        _first(record, "calleeNumber", "callee_number") or callee["number"]
    )
    caller_name = _stringify(
        caller["name"] or _first(record, "userDisplayName", "callerName")
    )
    callee_name = _stringify(
        callee["name"] or _first(record, "userDisplayName", "calleeName")
    )

    phone = caller_number
    normalized_phone = normalize_phone(phone) if phone else None
    display_name = caller_name or phone or "Unknown Teams caller"

    occurred_at = _parse_datetime(
        _first(record, "startDateTime", "start_date_time", "startTime", "start_time")
    )
    duration = parse_iso_duration_seconds(
        _first(record, "duration", "duration_seconds", "callDuration")
    )
    if duration is None:
        started = _parse_optional_datetime(_first(record, "startDateTime"))
        ended = _parse_optional_datetime(_first(record, "endDateTime"))
        if started and ended and ended >= started:
            duration = int((ended - started).total_seconds())

    result = _stringify(
        _first(record, "result", "callResult", "finalSipCode", "destinationContext")
    )
    call_type = _stringify(_first(record, "callType", "type", "call_type"))
    join_url = _stringify(_first(record, "joinWebUrl", "join_web_url"))
    user_principal_name = _stringify(
        _first(record, "userPrincipalName", "user_principal_name")
    )

    body_parts = [
        f"Microsoft Teams {direction} call",
        f"Call type: {call_type}" if call_type else None,
        f"Answered by: {callee_name}" if callee_name else None,
        f"Duration: {duration} seconds" if duration is not None else None,
        f"Result: {result}" if result else None,
        f"Join link: {join_url}" if join_url else None,
    ]

    return {
        "external_ref": f"teams_voice:call:{call_id}",
        "direction": direction,
        "subject": f"Teams {direction} call: {display_name}",
        "summary": result or f"Microsoft Teams {direction} call",
        "body": "\n".join(part for part in body_parts if part),
        "occurred_at": occurred_at,
        "participants": {
            "provider": TEAMS_VOICE_PROVIDER,
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
            "call_type": call_type,
            "join_web_url": join_url,
            "user_principal_name": user_principal_name,
            "raw": record,
        },
    }


# Provider-owned keys that reconciliation may refresh even after intake staff
# have curated a captured call. Everything else (the corrected caller identity,
# the narrative, the contact link) belongs to the humans who worked the call.
_PROVIDER_OWNED_PARTICIPANT_KEYS = frozenset(
    {
        "provider",
        "call_id",
        "callee_name",
        "caller_number",
        "callee_number",
        "direction",
        "result",
        "duration_seconds",
        "call_type",
        "join_web_url",
        "user_principal_name",
        "raw",
        "webhook_subscription_id",
        "webhook_change_type",
    }
)


async def import_teams_voice_records(
    db: AsyncSession,
    *,
    tenant_id: str,
    records: list[dict[str, Any]],
) -> TeamsVoiceImportResult:
    """Idempotently import Teams voice records as ``CommunicationLog`` rows."""
    result = TeamsVoiceImportResult()
    tenant_uuid = uuid.UUID(str(tenant_id))

    for record in records:
        normalized = normalize_teams_voice_record(record)
        if not normalized:
            result.skipped += 1
            continue

        values = {
            "tenant_id": tenant_uuid,
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
                index_where=text("external_ref LIKE 'teams_voice:call:%'"),
            )
            .returning(CommunicationLog.id)
        )
        if inserted_id:
            result.imported += 1
            participants = normalized["participants"]
            result.captured.append(
                {
                    "communication_id": str(inserted_id),
                    "caller_name": participants.get("caller_name"),
                    "caller_number": participants.get("caller_number"),
                    "callee_name": participants.get("callee_name"),
                    "duration_seconds": participants.get("duration_seconds"),
                    "result": participants.get("result"),
                    "occurred_at": normalized["occurred_at"],
                }
            )
            continue

        existing = await db.scalar(
            select(CommunicationLog)
            .where(
                CommunicationLog.tenant_id == tenant_uuid,
                CommunicationLog.external_ref == normalized["external_ref"],
            )
            .with_for_update()
        )
        if existing is None:
            raise TeamsVoiceError("Teams voice call upsert lost its canonical row.")

        captured_by_staff = bool(existing.created_by_user_id or existing.contact_id)
        if captured_by_staff:
            merged = dict(existing.participants or {})
            for key in _PROVIDER_OWNED_PARTICIPANT_KEYS:
                if key in normalized["participants"]:
                    merged[key] = normalized["participants"][key]
            if merged == (existing.participants or {}):
                result.skipped += 1
                continue
            existing.participants = merged
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


# ── Change notifications ─────────────────────────────────────────────────


_RESOURCE_CALL_ID = re.compile(
    r"callRecords(?:\('(?P<quoted>[^']+)'\)|/(?P<path>[^/?#]+))"
)


def _call_id_from_resource(resource: str | None) -> str | None:
    """Pull the call-record id out of a notification's ``resource`` string.

    Graph writes this either as ``communications/callRecords('<id>')`` or as
    ``communications/callRecords/<id>``, so both forms are matched rather than
    assuming one shape.
    """
    if not resource:
        return None
    match = _RESOURCE_CALL_ID.search(resource)
    if not match:
        return None
    return match.group("quoted") or match.group("path") or None


def teams_voice_webhook_jobs(
    body: dict[str, Any],
    *,
    subscription_id: str | None = None,
) -> list[TeamsVoiceWebhookJob]:
    """Turn a Graph change-notification batch into durable work.

    Only the call-record id is carried forward. The notification itself is not
    trusted for call content: the worker re-reads the record from Graph with
    our own app-only token.
    """
    notifications = body.get("value")
    if not isinstance(notifications, list):
        return []

    jobs: list[TeamsVoiceWebhookJob] = []
    seen: set[str] = set()
    for item in notifications:
        if not isinstance(item, dict):
            continue
        change_type = _stringify(item.get("changeType"))
        if change_type not in {None, "created", "updated"}:
            continue
        item_subscription = _stringify(item.get("subscriptionId"))
        if (
            subscription_id
            and item_subscription
            and not hmac.compare_digest(subscription_id, item_subscription)
        ):
            # A notification for a subscription this tenant does not own.
            continue

        resource_data = item.get("resourceData")
        call_id = None
        if isinstance(resource_data, dict):
            call_id = _stringify(_first(resource_data, "id", "callRecordId"))
        if not call_id:
            call_id = _call_id_from_resource(_stringify(item.get("resource")))
        if not call_id:
            continue

        key = f"teams_voice_call:{call_id}"
        if key in seen:
            continue
        seen.add(key)
        jobs.append(
            TeamsVoiceWebhookJob(
                idempotency_key=key,
                payload={
                    "call_record_id": call_id,
                    "change_type": change_type,
                    "subscription_id": item_subscription,
                },
            )
        )
    return jobs


def verify_client_state(expected: str | None, received: str | None) -> bool:
    """Constant-time ``clientState`` check on an inbound notification."""
    if not expected or not received:
        return False
    return hmac.compare_digest(str(expected), str(received))


def generate_client_state() -> str:
    return secrets.token_urlsafe(32)


# ── Settings row ─────────────────────────────────────────────────────────


async def get_voice_settings(
    db: AsyncSession, *, tenant_id: str | uuid.UUID
) -> TeamsVoiceSetting | None:
    result = await db.execute(
        select(TeamsVoiceSetting).where(
            TeamsVoiceSetting.tenant_id == uuid.UUID(str(tenant_id))
        )
    )
    return result.scalar_one_or_none()


async def upsert_voice_settings(
    db: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
    entra_tenant_id: str | None = None,
    is_enabled: bool | None = None,
    configured_by_user_id: uuid.UUID | None = None,
) -> TeamsVoiceSetting:
    """Create or update the tenant's voice configuration row."""
    row = await get_voice_settings(db, tenant_id=tenant_id)
    if row is None:
        row = TeamsVoiceSetting(tenant_id=uuid.UUID(str(tenant_id)))
        db.add(row)

    if entra_tenant_id is not None:
        validated = valid_entra_tenant_id(entra_tenant_id)
        if not validated:
            raise TeamsVoiceError(
                "Enter your Microsoft Entra directory (tenant) ID — the GUID "
                "from Entra admin center → Overview. 'common' cannot be used "
                "for application-permission access."
            )
        if row.entra_tenant_id != validated:
            # Pointing at a different directory invalidates the subscription
            # created under the old one.
            row.subscription_id = None
            row.subscription_expires_at = None
        row.entra_tenant_id = validated

    if is_enabled is not None:
        row.is_enabled = is_enabled

    if configured_by_user_id is not None:
        row.configured_by_user_id = configured_by_user_id

    if not row.encrypted_client_state:
        row.encrypted_client_state = encrypt_token(generate_client_state())

    await db.flush()
    return row


def client_state_of(row: TeamsVoiceSetting) -> str | None:
    if not row.encrypted_client_state:
        return None
    try:
        return decrypt_token(row.encrypted_client_state)
    except Exception:
        logger.warning(
            "Could not decrypt Teams voice clientState for tenant %s",
            row.tenant_id,
            exc_info=True,
        )
        return None


# ── App-only token ───────────────────────────────────────────────────────


async def get_voice_app_credentials(
    db: AsyncSession, *, tenant_id: str | uuid.UUID
) -> TeamsVoiceAppCredentials:
    """Resolve the Entra app used for the client-credentials grant.

    A firm that registers its own single-tenant app (the easier security
    conversation, since it needs exactly one application permission) is
    preferred; otherwise the platform's multi-tenant app is used.
    """
    app = await get_tenant_oauth_app(
        db, tenant_id=tenant_id, provider=TEAMS_VOICE_APP_PROVIDER
    )
    if app:
        return TeamsVoiceAppCredentials(
            client_id=decrypt_token(app.encrypted_client_id),
            client_secret=decrypt_token(app.encrypted_client_secret),
            source="tenant",
        )
    if settings.MICROSOFT_CLIENT_ID and settings.MICROSOFT_CLIENT_SECRET:
        return TeamsVoiceAppCredentials(
            client_id=settings.MICROSOFT_CLIENT_ID,
            client_secret=settings.MICROSOFT_CLIENT_SECRET,
            source="platform",
        )
    raise TeamsVoiceNotConfigured(
        "No Microsoft application credentials are configured for Teams voice."
    )


async def get_app_only_token(
    db: AsyncSession,
    *,
    tenant_id: str | uuid.UUID,
    require_enabled: bool = True,
) -> str:
    """Acquire an app-only Graph token for the tenant's Entra directory.

    ``require_enabled=False`` is for teardown: removing a subscription at
    Microsoft has to work *after* the tenant has been switched off, or
    disabling voice would only stop us storing what Graph keeps sending.
    """
    row = await get_voice_settings(db, tenant_id=tenant_id)
    if not row:
        raise TeamsVoiceNotConfigured("Teams voice capture is not configured.")
    if require_enabled and not row.is_enabled:
        raise TeamsVoiceNotConfigured("Teams voice capture is not enabled.")
    if not row.entra_tenant_id:
        raise TeamsVoiceNotConfigured(
            "Teams voice is missing the Microsoft Entra directory (tenant) ID."
        )

    credentials = await get_voice_app_credentials(db, tenant_id=tenant_id)
    token_url = (
        f"https://login.microsoftonline.com/{row.entra_tenant_id}" "/oauth2/v2.0/token"
    )
    data = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scope": TEAMS_VOICE_GRAPH_SCOPE,
        "grant_type": "client_credentials",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(token_url, data=data)
    except httpx.HTTPError as exc:
        raise TeamsVoiceError(
            f"Could not reach Microsoft Entra for a Teams voice token: {exc}"
        ) from exc

    if resp.status_code != 200:
        detail = resp.text[:300]
        logger.warning(
            "Teams voice app-only token failed for tenant %s: %s %s",
            tenant_id,
            resp.status_code,
            detail,
        )
        if "AADSTS7000215" in detail or "AADSTS7000222" in detail:
            raise TeamsVoicePermanentError(
                "Microsoft rejected the application secret for Teams voice. "
                "Generate a new client secret and save it again."
            )
        if "AADSTS700016" in detail or "AADSTS90002" in detail:
            raise TeamsVoicePermanentError(
                "Microsoft does not recognize this application in that "
                "directory. Check the Entra tenant ID and that admin consent "
                "was granted."
            )
        raise TeamsVoiceError(
            f"Microsoft Entra refused the Teams voice token request "
            f"({resp.status_code})."
        )

    token = _stringify(resp.json().get("access_token"))
    if not token:
        raise TeamsVoiceError("Microsoft Entra returned no access token.")
    return token


async def _graph_request(
    method: str,
    path: str,
    *,
    token: str,
    json_body: dict | None = None,
    max_retries: int = 3,
) -> httpx.Response | None:
    """Issue an app-only Graph request, honoring 429 ``Retry-After``."""
    url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    attempt = 0
    async with httpx.AsyncClient(timeout=45) as client:
        while True:
            try:
                resp = await client.request(
                    method, url, headers=headers, json=json_body
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "Teams voice Graph %s %s network error: %s", method, path, exc
                )
                return None

            if resp.status_code != 429:
                return resp

            attempt += 1
            if attempt > max_retries:
                logger.warning(
                    "Teams voice Graph %s %s throttled, retries exhausted",
                    method,
                    path,
                )
                return resp
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2**attempt
            except ValueError:
                delay = 2**attempt
            await asyncio.sleep(delay)


def _graph_error(resp: httpx.Response | None, context: str) -> TeamsVoiceError:
    if resp is None:
        return TeamsVoiceError(f"Microsoft Graph did not respond while {context}.")
    detail = resp.text[:300]
    if resp.status_code in (401, 403):
        return TeamsVoicePermanentError(
            f"Microsoft Graph denied access while {context} ({resp.status_code}). "
            f"Grant admin consent for {TEAMS_VOICE_APP_ROLE} on the application."
        )
    if resp.status_code == 404:
        return TeamsVoicePermanentError(
            f"Microsoft Graph found nothing while {context} (404)."
        )
    logger.warning("Teams voice Graph error while %s: %s", context, detail)
    return TeamsVoiceError(
        f"Microsoft Graph failed while {context} ({resp.status_code})."
    )


async def _graph_collect(
    path: str, *, token: str, context: str
) -> list[dict[str, Any]]:
    """GET a Graph collection, following ``@odata.nextLink``."""
    items: list[dict[str, Any]] = []
    next_path: str | None = path
    pages = 0
    while next_path and pages < _PAGE_LIMIT:
        pages += 1
        resp = await _graph_request("GET", next_path, token=token)
        if resp is None or resp.status_code != 200:
            raise _graph_error(resp, context)
        payload = resp.json()
        items.extend(v for v in payload.get("value", []) if isinstance(v, dict))
        next_path = payload.get("@odata.nextLink")
    if next_path:
        logger.warning("Teams voice paging for %s hit the page cap", context)
    return items


# ── Graph reads ──────────────────────────────────────────────────────────


async def fetch_pstn_calls(
    db: AsyncSession, *, tenant_id: str, days: int = 7
) -> list[dict[str, Any]]:
    """Fetch the PSTN usage report for the window.

    Graph caps this report at a 90-day span and publishes it with a lag, which
    is exactly why it is the reconciliation feed and not the primary one.
    """
    token = await get_app_only_token(db, tenant_id=tenant_id)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(1, min(days, 90)))
    path = (
        "/communications/callRecords/getPstnCalls"
        f"(fromDateTime={start.strftime('%Y-%m-%dT%H:%M:%SZ')},"
        f"toDateTime={now.strftime('%Y-%m-%dT%H:%M:%SZ')})"
    )
    return await _graph_collect(
        path, token=token, context="reading the Teams PSTN call report"
    )


async def fetch_call_record(
    db: AsyncSession, *, tenant_id: str, call_record_id: str
) -> dict[str, Any]:
    """Read one call record, expanded far enough to see PSTN endpoints."""
    token = await get_app_only_token(db, tenant_id=tenant_id)
    path = (
        f"/communications/callRecords/{call_record_id}"
        "?$expand=sessions($expand=segments)"
    )
    resp = await _graph_request("GET", path, token=token)
    if resp is None or resp.status_code != 200:
        raise _graph_error(resp, "reading a Teams call record")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise TeamsVoicePermanentError("Microsoft Graph returned an unusable record.")
    return payload


async def probe_voice_connection(db: AsyncSession, *, tenant_id: str) -> dict[str, Any]:
    """Verify credentials and permission end-to-end for the admin test button."""
    records = await fetch_pstn_calls(db, tenant_id=tenant_id, days=1)
    inbound = [r for r in records if _pstn_direction(r) == "inbound"]
    return {
        "status": "ok",
        "sample_count": len(records),
        "inbound_count": len(inbound),
    }


# ── Subscription lifecycle ───────────────────────────────────────────────


def _subscription_body(notification_url: str, client_state: str) -> dict[str, Any]:
    expiration = datetime.now(timezone.utc) + timedelta(minutes=SUBSCRIPTION_MINUTES)
    return {
        "changeType": "created",
        "notificationUrl": notification_url,
        "resource": "communications/callRecords",
        "expirationDateTime": expiration.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),
        "clientState": client_state,
    }


async def ensure_subscription(
    db: AsyncSession,
    *,
    tenant_id: str,
    notification_url: str,
) -> TeamsVoiceSetting:
    """Create or renew the tenant's call-record change subscription.

    Graph validates ``notificationUrl`` synchronously by POSTing a
    ``validationToken`` to it, so this only succeeds against a publicly
    reachable deployment.
    """
    row = await get_voice_settings(db, tenant_id=tenant_id)
    if not row or not row.is_enabled:
        raise TeamsVoiceNotConfigured("Teams voice capture is not enabled.")
    client_state = client_state_of(row)
    if not client_state:
        client_state = generate_client_state()
        row.encrypted_client_state = encrypt_token(client_state)
        await db.flush()

    token = await get_app_only_token(db, tenant_id=tenant_id)

    if row.subscription_id:
        expiration = datetime.now(timezone.utc) + timedelta(
            minutes=SUBSCRIPTION_MINUTES
        )
        resp = await _graph_request(
            "PATCH",
            f"/subscriptions/{row.subscription_id}",
            token=token,
            json_body={
                "expirationDateTime": expiration.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
            },
        )
        if resp is not None and resp.status_code == 200:
            row.subscription_expires_at = _parse_optional_datetime(
                resp.json().get("expirationDateTime")
            )
            row.notification_url = notification_url
            await db.flush()
            return row
        # A subscription Graph no longer knows about is recreated below rather
        # than left pointing at a dead id.
        logger.info(
            "Teams voice subscription renewal failed for tenant %s; recreating",
            tenant_id,
        )
        row.subscription_id = None
        row.subscription_expires_at = None

    resp = await _graph_request(
        "POST",
        "/subscriptions",
        token=token,
        json_body=_subscription_body(notification_url, client_state),
    )
    if resp is None or resp.status_code not in (200, 201):
        raise _graph_error(resp, "creating the Teams call-record subscription")

    payload = resp.json()
    row.subscription_id = _stringify(payload.get("id"))
    row.subscription_expires_at = _parse_optional_datetime(
        payload.get("expirationDateTime")
    )
    row.notification_url = notification_url
    await db.flush()
    return row


async def delete_subscription(db: AsyncSession, *, tenant_id: str) -> bool:
    """Remove the tenant's subscription; always clears local state."""
    row = await get_voice_settings(db, tenant_id=tenant_id)
    if not row or not row.subscription_id:
        return False
    subscription_id = row.subscription_id
    # Local state is cleared regardless: leaving a stale id behind would make
    # the next enable try to renew a subscription that may not be ours.
    row.subscription_id = None
    row.subscription_expires_at = None
    await db.flush()
    try:
        token = await get_app_only_token(db, tenant_id=tenant_id, require_enabled=False)
    except TeamsVoiceError:
        logger.warning(
            "Cleared the local Teams voice subscription for tenant %s but could "
            "not reach Microsoft to remove it",
            tenant_id,
            exc_info=True,
        )
        return False
    resp = await _graph_request(
        "DELETE", f"/subscriptions/{subscription_id}", token=token
    )
    return bool(resp is not None and resp.status_code in (200, 204, 404))


def subscription_needs_renewal(row: TeamsVoiceSetting) -> bool:
    if not row.is_enabled:
        return False
    if not row.subscription_id or not row.subscription_expires_at:
        return True
    expires = row.subscription_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires - datetime.now(timezone.utc) <= SUBSCRIPTION_RENEW_BEFORE


# ── Entry points used by the worker / scheduler ──────────────────────────


async def import_teams_voice_webhook_job(
    db: AsyncSession,
    *,
    tenant_id: str,
    payload: dict[str, Any],
) -> TeamsVoiceImportResult:
    """Resolve and import one durable webhook payload."""
    call_record_id = _stringify(payload.get("call_record_id"))
    if not call_record_id:
        raise ValueError("Teams voice durable event is missing a call record ID.")

    record = await fetch_call_record(
        db, tenant_id=tenant_id, call_record_id=call_record_id
    )
    returned_id = _stringify(record.get("id"))
    if returned_id and not hmac.compare_digest(call_record_id, returned_id):
        raise TeamsVoicePermanentError(
            "Microsoft Graph returned a different call record than requested."
        )

    enriched = {
        **record,
        "canonical_call_id": call_record_id,
        "webhook_subscription_id": _stringify(payload.get("subscription_id")),
        "webhook_change_type": _stringify(payload.get("change_type")),
    }
    return await import_teams_voice_records(db, tenant_id=tenant_id, records=[enriched])


async def sync_teams_voice_call_history(
    db: AsyncSession,
    *,
    tenant_id: str,
    days: int = 7,
) -> TeamsVoiceImportResult:
    """Reconcile against the PSTN usage report."""
    records = await fetch_pstn_calls(db, tenant_id=tenant_id, days=days)
    result = await import_teams_voice_records(db, tenant_id=tenant_id, records=records)
    row = await get_voice_settings(db, tenant_id=tenant_id)
    if row:
        row.last_sync_at = datetime.now(timezone.utc)
        row.last_sync_status = "ok"
        row.last_sync_error = None
        await db.flush()
    return result
