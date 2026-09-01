"""Fail-closed Twilio adapter and tenant-scoped SMS reconciliation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.conversion_loop import LeadChannelConsent, SmsConsentEvent
from app.models.matter_party import MatterParty
from app.models.matter_assignment import MatterAssignment
from app.models.operator_audit import OperatorAuditLog
from app.models.plugin import Matter
from app.models.rbac import Role, UserRole
from app.models.sms import (
    SmsMessage,
    SmsNumberSuppression,
    SmsNumberSuppressionEvent,
    SmsProviderConfig,
    SmsProviderCredential,
    SmsReviewItem,
)
from app.models.task import TaskAutomationRun
from app.models.tenant import Tenant
from app.models.user import User
from app.services.matter_access import can_access_matter
from app.services.operator_audit import sanitize_operator_metadata
from app.services.rbac_service import get_user_capabilities
from app.services.token_vault import decrypt_token


_DETERMINISTIC_PROVIDER_REJECTION_CODES = frozenset({400, 401, 403, 404, 422})
_TRANSIENT_PROVIDER_HTTP_CODES = frozenset({408, 409, 425, 429})
_MAX_RETAINED_PROVIDER_GENERATIONS = 5


class SmsError(ValueError):
    def __init__(
        self,
        message: str,
        status_code: int = 422,
        *,
        delivery_certainty: str = "not_attempted",
        sms_message_id: uuid.UUID | None = None,
        reconciliation_required: bool = False,
        code: str = "sms_error",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.delivery_certainty = delivery_certainty
        self.sms_message_id = sms_message_id
        self.reconciliation_required = reconciliation_required
        self.code = code

    def api_detail(self) -> dict[str, object]:
        return {
            "message": str(self),
            "code": self.code,
            "delivery_certainty": self.delivery_certainty,
            "sms_message_id": (
                str(self.sms_message_id) if self.sms_message_id else None
            ),
            "reconciliation_required": self.reconciliation_required,
        }


async def lock_provider_config_admission(db: AsyncSession, *, tenant_id) -> None:
    """Serialize durable send admission with provider credential rotation."""
    await db.execute(
        text(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended(:sms_provider_lock, 0))"
        ),
        {
            "sms_provider_lock": (
                f"lawhand:sms-provider-config-admission:v1:{tenant_id}:twilio"
            )
        },
    )


def normalize_e164(value: str | None) -> str:
    raw = re.sub(r"[ ().-]", "", str(value or ""))
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if not re.fullmatch(r"\+[1-9]\d{7,14}", raw):
        raise SmsError("A verified E.164 mobile destination is required", 422)
    return raw


def twilio_signature(*, auth_token: str, url: str, params: dict[str, str]) -> str:
    canonical = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    # Twilio's X-Twilio-Signature protocol mandates HMAC-SHA1. This is message
    # authentication with a high-entropy provider secret, not password hashing;
    # changing the digest would reject every authentic provider callback.
    # codeql[py/weak-sensitive-data-hashing]
    digest = hmac.new(auth_token.encode(), canonical.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio_signature(
    *, auth_token: str, url: str, params: dict[str, str], supplied: str | None
) -> bool:
    if not supplied or not auth_token:
        return False
    expected = twilio_signature(auth_token=auth_token, url=url, params=params)
    return hmac.compare_digest(expected, supplied.strip())


def _parse_time(value: str | None) -> time | None:
    if not value or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        return None
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return time(hour, minute)
    except (TypeError, ValueError):
        return None


def in_quiet_hours(*, consent: LeadChannelConsent, now: datetime) -> bool:
    start = _parse_time(consent.quiet_hours_start)
    end = _parse_time(consent.quiet_hours_end)
    if not start or not end or not consent.consent_timezone:
        return True
    try:
        local = now.astimezone(ZoneInfo(consent.consent_timezone)).time()
    except ZoneInfoNotFoundError:
        return True
    if start == end:
        return True
    if start < end:
        return start <= local < end
    return local >= start or local < end


def quiet_hours_configuration_valid(consent: LeadChannelConsent) -> bool:
    start = _parse_time(consent.quiet_hours_start)
    end = _parse_time(consent.quiet_hours_end)
    if not start or not end or start == end or not consent.consent_timezone:
        return False
    try:
        ZoneInfo(consent.consent_timezone)
    except ZoneInfoNotFoundError:
        return False
    return True


_KNOWN_PROVIDER_STATUSES = frozenset(
    {
        "queued",
        "accepted",
        "sending",
        "sent",
        "delivered",
        "read",
        "undelivered",
        "failed",
    }
)
_TERMINAL_PROVIDER_STATUSES = frozenset({"delivered", "read", "undelivered", "failed"})
_DISPATCH_LEASE = timedelta(minutes=2)
_PROVIDER_CREATED_BEFORE_DISPATCH = timedelta(minutes=2)
_PROVIDER_CREATED_AFTER_DISPATCH = timedelta(minutes=5)
_STOP_KEYWORDS = frozenset({"STOP", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"})
_START_KEYWORDS = frozenset({"START", "UNSTOP"})


def _provider_status_rank(value: str | None) -> int:
    return {
        "queued": 10,
        "accepted": 20,
        "sending": 30,
        "sent": 40,
        "delivered": 50,
        "read": 60,
        "undelivered": 70,
        "failed": 70,
    }.get((value or "").lower(), 0)


def provider_status_transition_allowed(
    *, current: str | None, incoming: str | None
) -> bool:
    """Accept provider progress without letting retries regress settled truth."""
    normalized_current = (current or "").lower()
    normalized_incoming = (incoming or "").lower()
    if normalized_incoming not in _KNOWN_PROVIDER_STATUSES:
        return False
    if normalized_incoming == normalized_current:
        return False
    if normalized_current in _TERMINAL_PROVIDER_STATUSES:
        return normalized_current == "delivered" and normalized_incoming == "read"
    return _provider_status_rank(normalized_incoming) >= _provider_status_rank(
        normalized_current
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_provider_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    try:
        parsed = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    return _as_utc(parsed)


def _append_sms_outcome_audit(
    db: AsyncSession,
    *,
    message: SmsMessage,
    actor_user_id,
    outcome: str,
    metadata: dict | None = None,
    actor_type: str | None = None,
) -> None:
    """Append sanitized evidence in the same transaction as SMS truth."""
    db.add(
        OperatorAuditLog(
            action=f"sms.dispatch.{outcome}",
            actor_type=(actor_type or ("tenant_user" if actor_user_id else "provider")),
            actor_id=str(actor_user_id) if actor_user_id else None,
            resource_type="sms_message",
            resource_id=str(message.id),
            metadata_json=sanitize_operator_metadata(
                {
                    "tenant_id": str(message.tenant_id),
                    "contact_id": str(message.contact_id)
                    if message.contact_id
                    else None,
                    "matter_id": str(message.matter_id) if message.matter_id else None,
                    "category": message.category,
                    "status": message.status,
                    "delivery_certainty": message.delivery_certainty,
                    "provider": "twilio",
                    **(metadata or {}),
                }
            ),
        )
    )


async def _lock_sms_actor(
    db: AsyncSession,
    *,
    tenant_id,
    user_id,
    required_capabilities: frozenset[str],
) -> User | None:
    """Lock and revalidate one live actor and the actor's persisted roles."""

    user = await db.scalar(
        select(User)
        .where(User.id == user_id, User.tenant_id == tenant_id)
        .with_for_update()
    )
    if user is None or not user.is_active:
        return None
    role_rows = (
        await db.execute(
            select(UserRole, Role)
            .join(
                Role,
                (Role.id == UserRole.role_id) & (Role.tenant_id == UserRole.tenant_id),
            )
            .where(
                UserRole.user_id == user_id,
                UserRole.tenant_id == tenant_id,
                Role.tenant_id == tenant_id,
            )
            .order_by(Role.id, UserRole.id)
            .with_for_update(of=(UserRole, Role))
        )
    ).all()
    capabilities: set[str] = set()
    for _assignment, assigned_role in role_rows:
        capabilities.update(assigned_role.capabilities or [])
    if not required_capabilities.issubset(capabilities):
        return None
    return user


async def _lock_sms_matter_access(
    db: AsyncSession,
    *,
    tenant_id,
    user: User,
    matter_id,
) -> tuple[Matter, str] | None:
    """Lock and revalidate the actor's current access to one matter."""

    matter = await db.scalar(
        select(Matter)
        .where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
        .with_for_update(of=Matter)
    )
    if matter is None:
        return None
    actor_binding = "admin" if user.role == "admin" else None
    assignment = None
    if actor_binding is None and matter.user_id == user.id:
        actor_binding = "owner"
    elif actor_binding is None:
        assignment = await db.scalar(
            select(MatterAssignment)
            .where(
                MatterAssignment.tenant_id == tenant_id,
                MatterAssignment.matter_id == matter_id,
                MatterAssignment.user_id == user.id,
            )
            .with_for_update(of=MatterAssignment)
        )
        if assignment is not None:
            actor_binding = "assignment"
    if actor_binding is None:
        return None
    return matter, actor_binding


async def _lock_sms_matter_authorization(
    db: AsyncSession,
    *,
    tenant_id,
    user_id,
    matter_id,
    contact_id,
    required_capabilities: frozenset[str],
) -> dict[str, str] | None:
    """Fence live actor capability, matter access, and recipient binding.

    The user, that user's role assignments, and the assigned roles are locked
    across provider I/O.  A deactivation or capability revocation therefore
    either commits before this fence and blocks the operation, or waits until
    the already-authorized provider attempt has durably recorded its outcome.
    Locks are actor-local; unrelated tenant users remain concurrent.
    """
    user = await _lock_sms_actor(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        required_capabilities=required_capabilities,
    )
    if user is None:
        return None
    access = await _lock_sms_matter_access(
        db,
        tenant_id=tenant_id,
        user=user,
        matter_id=matter_id,
    )
    if access is None:
        return None
    matter, actor_binding = access
    if matter.client_contact_id == contact_id:
        return {"actor_binding": actor_binding, "recipient_binding": "client"}
    party = await db.scalar(
        select(MatterParty)
        .where(
            MatterParty.tenant_id == tenant_id,
            MatterParty.matter_id == matter_id,
            MatterParty.contact_id == contact_id,
        )
        .order_by(MatterParty.id)
        .limit(1)
        .with_for_update(of=MatterParty)
    )
    if party is None:
        return None
    return {
        "actor_binding": actor_binding,
        "recipient_binding": "matter_party",
        "party_id": str(party.id),
    }


async def _sms_matter_authorization_preflight(
    db: AsyncSession,
    *,
    tenant_id,
    user_id,
    matter_id,
    contact_id,
    required_capabilities: frozenset[str],
) -> bool:
    """Check replay eligibility without locking inbound-routing rows.

    This privacy-preserving check runs before idempotency replay lookup so an
    unauthorized caller cannot enumerate prior sends. It is intentionally
    non-locking: a new reservation may wait for an in-flight STOP's Contact FK
    without holding Matter. The locking fence is repeated after reservation
    and before provider I/O, so concurrent revocation still fails closed.
    """

    user = await db.scalar(
        select(User).where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )
    if user is None:
        return False
    capabilities = await get_user_capabilities(db, user.id)
    if not required_capabilities.issubset(capabilities):
        return False
    if not await can_access_matter(
        db,
        tenant_id=tenant_id,
        user_id=user.id,
        is_admin=user.role == "admin",
        matter_id=matter_id,
    ):
        return False
    matter = await db.scalar(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    if matter is None:
        return False
    if matter.client_contact_id == contact_id:
        return True
    return (
        await db.scalar(
            select(MatterParty.id)
            .where(
                MatterParty.tenant_id == tenant_id,
                MatterParty.matter_id == matter_id,
                MatterParty.contact_id == contact_id,
            )
            .limit(1)
        )
        is not None
    )


async def _lock_sms_replay(
    db: AsyncSession,
    *,
    tenant_id,
    user_id,
    matter_id,
    contact_id,
    idempotency_key: str,
    required_capabilities: frozenset[str],
) -> SmsMessage | None:
    """Lock and authorize replay truth in the global message/target order."""

    replay = await db.scalar(
        select(SmsMessage)
        .where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if replay is None:
        return None

    contact = await db.scalar(
        select(Contact)
        .where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
        .with_for_update()
    )
    if contact is None:
        raise SmsError(
            "SMS target was not found",
            404,
            code="sms_target_not_found",
        )
    # STOP locks every consent associated with the Contact before Matter.
    # Replay does not require current consent, but it must share that ordering
    # before rechecking the actor/Matter/party authorization fence.
    await load_sms_consents(db, tenant_id, contact_id, lock=True)
    if (
        await _lock_sms_matter_authorization(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            matter_id=matter_id,
            contact_id=contact_id,
            required_capabilities=required_capabilities,
        )
        is None
    ):
        raise SmsError(
            "SMS target was not found",
            404,
            code="sms_target_not_found",
        )
    return replay


def _provider_lookup_matches_reserved_dispatch(
    *,
    message: SmsMessage,
    payload: dict,
    lookup_sid: str,
    account_sid: str,
) -> bool:
    """Bind lookup truth to the exact reserved request, never just a SID."""
    if not message.provider_account_sid or message.provider_account_sid != account_sid:
        return False
    if message.provider_message_id and message.provider_message_id != lookup_sid:
        return False
    if str(payload.get("sid") or "") != lookup_sid:
        return False
    if str(payload.get("account_sid") or "") != account_sid:
        return False
    if str(payload.get("direction") or "").lower() != "outbound-api":
        return False
    expected_uri = f"/2010-04-01/Accounts/{account_sid}/Messages/{lookup_sid}.json"
    if str(payload.get("uri") or "") != expected_uri:
        return False
    try:
        provider_to = normalize_e164(payload.get("to"))
        provider_from = normalize_e164(payload.get("from"))
    except SmsError:
        return False
    if provider_to != message.to_number:
        return False
    provider_service = str(payload.get("messaging_service_sid") or "").strip() or None
    if provider_service != message.provider_messaging_service_sid:
        return False
    if message.from_number:
        try:
            stored_from = normalize_e164(message.from_number)
        except SmsError:
            return False
        if provider_from != stored_from:
            return False
    elif provider_service is None:
        # A fixed-sender reservation must know its sender before dispatch.  A
        # messaging service may assign one, but only a unique exact lookup may
        # bind that provider-selected value below.
        return False
    provider_body = str(payload.get("body") or "")
    if not hmac.compare_digest(provider_body, message.body):
        return False
    if message.contact_id is None or message.matter_id is None or not message.to_number:
        return False
    expected_digest = _request_digest(
        contact_id=message.contact_id,
        matter_id=message.matter_id,
        to_number=message.to_number,
        body=message.body,
        category=message.category,
    )
    if not hmac.compare_digest(expected_digest, message.request_digest):
        return False
    provider_created_at = _parse_provider_datetime(payload.get("date_created"))
    submission_started_at = _as_utc(message.provider_submission_started_at)
    if provider_created_at is None or submission_started_at is None:
        return False
    if not (
        submission_started_at - _PROVIDER_CREATED_BEFORE_DISPATCH
        <= provider_created_at
        <= submission_started_at + _PROVIDER_CREATED_AFTER_DISPATCH
    ):
        return False
    reserved_created_at = _as_utc(message.provider_created_at)
    if reserved_created_at is not None and (
        abs((provider_created_at - reserved_created_at).total_seconds()) > 1
    ):
        return False
    return True


async def _config(
    db: AsyncSession, tenant_id, *, lock_for_provider_io: bool = False
) -> SmsProviderConfig:
    statement = (
        select(SmsProviderConfig)
        .where(
            SmsProviderConfig.tenant_id == tenant_id,
            SmsProviderConfig.provider == "twilio",
            SmsProviderConfig.is_active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    if lock_for_provider_io:
        # A shared row fence allows concurrent provider operations while
        # forcing admin rotation/deactivation (FOR UPDATE) to wait until the
        # exact-generation provider outcome is durable.
        statement = statement.with_for_update(read=True)
    config = await db.scalar(statement)
    if (
        not config
        or not config.sender_ready
        or not str(config.account_sid or "").strip()
        or not config.encrypted_auth_token
        or not (
            str(config.messaging_service_sid or "").strip()
            or str(config.from_number or "").strip()
        )
    ):
        raise SmsError("SMS provider is not configured for delivery", 503)
    required_compliance = {
        "ownership_model",
        "consent_policy",
        "quiet_hours_policy",
    }
    if any(
        not str((config.compliance_snapshot or {}).get(key) or "").strip()
        for key in required_compliance
    ):
        raise SmsError("SMS provider compliance evidence is incomplete", 503)
    return config


def provider_auth_token(config: SmsProviderConfig | SmsProviderCredential) -> str:
    """Decrypt and validate the Twilio Account Auth Token at each use."""
    try:
        token = decrypt_token(config.encrypted_auth_token).strip()
    except Exception as exc:
        raise SmsError("SMS provider credentials are unavailable", 503) from exc
    if not token:
        raise SmsError("SMS provider credentials are unavailable", 503)
    return token


async def provider_credentials_for_generation(
    db: AsyncSession,
    *,
    tenant_id,
    generation: int,
    credential_id: uuid.UUID | None = None,
    lock_for_provider_io: bool = False,
) -> SmsProviderConfig | SmsProviderCredential:
    """Load exactly the credential generation bound to a durable message."""
    if credential_id is not None:
        bound_stmt = select(SmsProviderCredential).where(
            SmsProviderCredential.id == credential_id,
            SmsProviderCredential.tenant_id == tenant_id,
            SmsProviderCredential.provider == "twilio",
            SmsProviderCredential.generation == generation,
            SmsProviderCredential.retired_at.is_(None),
            SmsProviderCredential.encrypted_auth_token.is_not(None),
        )
        if lock_for_provider_io:
            bound_stmt = bound_stmt.with_for_update(read=True)
        bound = await db.scalar(bound_stmt)
        if bound is None:
            raise SmsError(
                "SMS provider credential generation is unavailable",
                409,
                code="sms_provider_generation_unavailable",
            )
        return bound
    current_stmt = select(SmsProviderConfig).where(
        SmsProviderConfig.tenant_id == tenant_id,
        SmsProviderConfig.provider == "twilio",
        SmsProviderConfig.generation == generation,
    )
    if lock_for_provider_io:
        current_stmt = current_stmt.with_for_update(read=True)
    current = await db.scalar(current_stmt)
    if current is not None and current.encrypted_auth_token:
        return current

    historical_stmt = select(SmsProviderCredential).where(
        SmsProviderCredential.tenant_id == tenant_id,
        SmsProviderCredential.provider == "twilio",
        SmsProviderCredential.generation == generation,
        SmsProviderCredential.retired_at.is_(None),
        SmsProviderCredential.encrypted_auth_token.is_not(None),
    )
    if lock_for_provider_io:
        historical_stmt = historical_stmt.with_for_update(read=True)
    historical = await db.scalar(historical_stmt)
    if historical is None:
        raise SmsError(
            "SMS provider credential generation is unavailable",
            409,
            code="sms_provider_generation_unavailable",
        )
    return historical


async def ensure_provider_config_credential(
    db: AsyncSession, *, config: SmsProviderConfig
) -> SmsProviderCredential:
    """Materialize one immutable identity row for the current config generation."""
    existing = await db.scalar(
        select(SmsProviderCredential).where(
            SmsProviderCredential.tenant_id == config.tenant_id,
            SmsProviderCredential.provider == config.provider,
            SmsProviderCredential.generation == config.generation,
        )
    )
    if existing is not None:
        exact = (
            existing.retired_at is None
            and existing.encrypted_auth_token is not None
            and existing.account_sid == config.account_sid
            and existing.messaging_service_sid == config.messaging_service_sid
            and existing.from_number == config.from_number
        )
        if exact:
            try:
                exact = hmac.compare_digest(
                    provider_auth_token(existing), provider_auth_token(config)
                )
            except SmsError:
                exact = False
        if not exact:
            raise SmsError(
                "SMS provider generation identity conflicts with stored credentials",
                409,
                code="sms_provider_generation_conflict",
            )
        return existing
    if not config.account_sid or not config.encrypted_auth_token:
        raise SmsError("SMS provider credentials are unavailable", 503)
    credential = SmsProviderCredential(
        tenant_id=config.tenant_id,
        provider=config.provider,
        generation=config.generation,
        account_sid=config.account_sid,
        encrypted_auth_token=config.encrypted_auth_token,
        messaging_service_sid=config.messaging_service_sid,
        from_number=config.from_number,
    )
    db.add(credential)
    await db.flush()
    return credential


async def archive_current_provider_credentials(
    db: AsyncSession,
    *,
    config: SmsProviderConfig,
    actor_user_id,
) -> list[SmsProviderCredential]:
    """Prepare a bounded rotation without retiring in-flight provider truth."""
    await ensure_provider_config_credential(db, config=config)
    retained = list(
        (
            await db.scalars(
                select(SmsProviderCredential)
                .where(
                    SmsProviderCredential.tenant_id == config.tenant_id,
                    SmsProviderCredential.provider == config.provider,
                    SmsProviderCredential.retired_at.is_(None),
                )
                .order_by(
                    SmsProviderCredential.generation.asc(),
                    SmsProviderCredential.id.asc(),
                )
                .with_for_update()
            )
        ).all()
    )
    retired: list[SmsProviderCredential] = []
    while len(retained) >= _MAX_RETAINED_PROVIDER_GENERATIONS:
        retirement_target = None
        for candidate in retained:
            unresolved = await db.scalar(
                select(SmsMessage.id)
                .where(
                    SmsMessage.tenant_id == config.tenant_id,
                    SmsMessage.direction == "outbound",
                    SmsMessage.provider_config_generation == candidate.generation,
                    or_(
                        SmsMessage.status.in_(
                            ["dispatching", "provider_unknown", "submitted"]
                        ),
                        and_(
                            SmsMessage.reconciliation_required_at.is_not(None),
                            SmsMessage.reconciliation_resolved_at.is_(None),
                        ),
                    ),
                )
                .limit(1)
            )
            if unresolved is None:
                retirement_target = candidate
                break
        if retirement_target is None:
            raise SmsError(
                "SMS credentials cannot rotate while every retained generation has unresolved messages",
                409,
                code="sms_provider_generation_retention_blocked",
            )
        retirement_target.encrypted_auth_token = None
        retirement_target.retired_at = datetime.now(timezone.utc)
        retirement_target.retired_by_user_id = actor_user_id
        retirement_target.retirement_reason = "bounded_rotation_after_resolution"
        retained.remove(retirement_target)
        retired.append(retirement_target)

    return retired


def append_sms_consent_event(
    db: AsyncSession,
    *,
    consent: LeadChannelConsent,
    contact_id,
    action: str,
    actor_type: str,
    actor_user_id=None,
    provider_message_id: str | None = None,
    metadata: dict | None = None,
) -> SmsConsentEvent:
    """Append the immutable, tenant-bound snapshot of a consent transition."""
    event = SmsConsentEvent(
        tenant_id=consent.tenant_id,
        consent_id=consent.id,
        lead_id=consent.lead_id,
        contact_id=contact_id,
        action=action,
        sms_status=consent.sms_status,
        sms_allowed=consent.sms_allowed,
        phone_verified=consent.phone_verified,
        mobile_e164=consent.mobile_e164,
        consented_at=consent.consented_at,
        consent_expires_at=consent.consent_expires_at,
        sms_revoked_at=consent.sms_revoked_at,
        consent_source=consent.consent_source,
        disclosure_version=consent.disclosure_version,
        consent_language=consent.consent_language,
        consent_timezone=consent.consent_timezone,
        quiet_hours_start=consent.quiet_hours_start,
        quiet_hours_end=consent.quiet_hours_end,
        allowed_categories=list(consent.allowed_categories or []),
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        provider_message_id=provider_message_id,
        metadata_json=dict(metadata or {}),
    )
    db.add(event)
    return event


async def load_sms_consents(
    db: AsyncSession, tenant_id, contact_id, *, lock: bool = False
) -> list[LeadChannelConsent]:
    statement = (
        select(LeadChannelConsent)
        .join(Lead, Lead.id == LeadChannelConsent.lead_id)
        .where(
            LeadChannelConsent.tenant_id == tenant_id,
            Lead.tenant_id == tenant_id,
            Lead.contact_id == contact_id,
        )
        .order_by(LeadChannelConsent.id)
    )
    if lock:
        statement = statement.with_for_update(of=LeadChannelConsent)
    return list((await db.scalars(statement)).all())


async def load_sms_consent(
    db: AsyncSession, tenant_id, contact_id, *, lock: bool = False
):
    rows = await load_sms_consents(db, tenant_id, contact_id, lock=lock)
    # Multiple linked leads are not an authorization signal. Conflicting,
    # revoked, or stale provenance must fail closed rather than be selected by
    # database order.
    return rows[0] if len(rows) == 1 else None


async def lock_sms_number_suppression(
    db: AsyncSession,
    *,
    tenant_id,
    mobile_e164: str,
    initial_suppressed: bool,
) -> SmsNumberSuppression:
    """Create-or-lock the tenant/number fence used by both STOP and dispatch."""
    normalized = normalize_e164(mobile_e164)
    await db.execute(
        pg_insert(SmsNumberSuppression)
        .values(
            tenant_id=tenant_id,
            mobile_e164=normalized,
            is_suppressed=initial_suppressed,
        )
        .on_conflict_do_nothing(constraint="uq_sms_number_suppressions_tenant_mobile")
    )
    row = await db.scalar(
        select(SmsNumberSuppression)
        .where(
            SmsNumberSuppression.tenant_id == tenant_id,
            SmsNumberSuppression.mobile_e164 == normalized,
        )
        .with_for_update()
    )
    if row is None:
        raise SmsError("SMS number suppression state is unavailable", 503)
    return row


def append_sms_number_suppression_event(
    db: AsyncSession,
    *,
    suppression: SmsNumberSuppression,
    action: str,
    keyword: str,
    provider_message_id: str | None,
) -> SmsNumberSuppressionEvent:
    event = SmsNumberSuppressionEvent(
        tenant_id=suppression.tenant_id,
        suppression_id=suppression.id,
        mobile_e164=suppression.mobile_e164,
        action=action,
        keyword=keyword,
        is_suppressed=suppression.is_suppressed,
        provider_message_id=provider_message_id,
    )
    db.add(event)
    return event


async def apply_compliance_keyword(
    db: AsyncSession,
    *,
    tenant_id,
    from_number: str,
    keyword: str,
    provider_message_id: str | None = None,
) -> dict[str, object]:
    """Lock phone identities and apply carrier keywords independently of routing."""
    from_number = normalize_e164(from_number)
    suppression = None
    if keyword in _STOP_KEYWORDS | _START_KEYWORDS:
        # The number row is always the first shared lock. A dispatch that owns
        # it may finish before STOP; a committed/in-flight STOP wins before any
        # later provider request, even when no identity can be routed.
        suppression = await lock_sms_number_suppression(
            db,
            tenant_id=tenant_id,
            mobile_e164=from_number,
            initial_suppressed=True,
        )
    # Phone normalization is not delegated to provider-specific SQL. Discover
    # candidates without locks, then lock only the exact matching identities in
    # a deterministic order and re-check their phone values under the lock.
    contact_candidates = list(
        (
            await db.execute(
                select(Contact.id, Contact.phone)
                .where(Contact.tenant_id == tenant_id, Contact.phone.isnot(None))
                .order_by(Contact.id)
            )
        ).all()
    )
    matched_contact_ids = []
    for contact_id, phone in contact_candidates:
        try:
            if normalize_e164(phone) == from_number:
                matched_contact_ids.append(contact_id)
        except SmsError:
            continue
    locked_candidates = []
    if matched_contact_ids:
        locked_candidates = list(
            (
                await db.scalars(
                    select(Contact)
                    .where(
                        Contact.tenant_id == tenant_id,
                        Contact.id.in_(matched_contact_ids),
                    )
                    .order_by(Contact.id)
                    .with_for_update()
                )
            ).all()
        )
    matched_contacts = []
    for contact in locked_candidates:
        try:
            if normalize_e164(contact.phone) == from_number:
                matched_contacts.append(contact)
        except SmsError:
            continue
    contact_ids = [contact.id for contact in matched_contacts]
    consents: list[LeadChannelConsent] = []
    if contact_ids:
        statement = (
            select(LeadChannelConsent)
            .join(Lead, Lead.id == LeadChannelConsent.lead_id)
            .where(
                LeadChannelConsent.tenant_id == tenant_id,
                Lead.tenant_id == tenant_id,
                Lead.contact_id.in_(contact_ids),
            )
            .order_by(LeadChannelConsent.id)
            .with_for_update(of=LeadChannelConsent)
        )
        consents = list((await db.scalars(statement)).all())
    linked_contact_id_by_lead = {}
    if consents:
        linked_contact_id_by_lead = dict(
            (
                await db.execute(
                    select(Lead.id, Lead.contact_id).where(
                        Lead.tenant_id == tenant_id,
                        Lead.id.in_([consent.lead_id for consent in consents]),
                    )
                )
            ).all()
        )

    now = datetime.now(timezone.utc)
    applied = False
    if keyword in _STOP_KEYWORDS:
        suppression.is_suppressed = True
        suppression.reason = "provider_stop"
        suppression.provider_message_id = provider_message_id
        suppression.suppressed_at = now
        suppression.released_at = None
        append_sms_number_suppression_event(
            db,
            suppression=suppression,
            action="provider_stop",
            keyword=keyword,
            provider_message_id=provider_message_id,
        )
        for contact in matched_contacts:
            contact.sms_opt_in = False
            contact.sms_opt_in_at = None
        for consent in consents:
            consent.sms_allowed = False
            consent.sms_status = "opted_out"
            consent.sms_revoked_at = now
            if not consent.email_allowed:
                consent.revoked_at = now
            append_sms_consent_event(
                db,
                consent=consent,
                contact_id=linked_contact_id_by_lead.get(consent.lead_id),
                action="provider_stop",
                actor_type="provider_customer",
                provider_message_id=provider_message_id,
                metadata={"keyword": keyword},
            )
        applied = True
    elif keyword in _START_KEYWORDS:
        consent = (
            consents[0] if len(matched_contacts) == 1 and len(consents) == 1 else None
        )
        can_restart = bool(
            consent
            and consent.phone_verified
            and consent.mobile_e164 == from_number
            and consent.disclosure_version
            and consent.allowed_categories
            and quiet_hours_configuration_valid(consent)
            and (not consent.consent_expires_at or consent.consent_expires_at > now)
        )
        if consent is not None:
            consent.sms_status = "active" if can_restart else "blocked"
            consent.sms_allowed = can_restart
        if not can_restart:
            suppression.is_suppressed = True
            suppression.reason = "provider_start_blocked"
            suppression.suppressed_at = now
            suppression.released_at = None
            append_sms_number_suppression_event(
                db,
                suppression=suppression,
                action="provider_start_blocked",
                keyword=keyword,
                provider_message_id=provider_message_id,
            )
            if consent is not None:
                append_sms_consent_event(
                    db,
                    consent=consent,
                    contact_id=matched_contacts[0].id,
                    action="provider_start_blocked",
                    actor_type="provider_customer",
                    provider_message_id=provider_message_id,
                    metadata={"keyword": keyword},
                )
        else:
            suppression.is_suppressed = False
            suppression.reason = "provider_start"
            suppression.provider_message_id = provider_message_id
            suppression.released_at = now
            append_sms_number_suppression_event(
                db,
                suppression=suppression,
                action="provider_start",
                keyword=keyword,
                provider_message_id=provider_message_id,
            )
            consent.sms_revoked_at = None
            consent.revoked_at = None
            consent.consented_at = now
            consent.consent_source = "provider_inbound_start"
            matched_contacts[0].sms_opt_in = True
            matched_contacts[0].sms_opt_in_at = now
            applied = True
            append_sms_consent_event(
                db,
                consent=consent,
                contact_id=matched_contacts[0].id,
                action="provider_start",
                actor_type="provider_customer",
                provider_message_id=provider_message_id,
                metadata={"keyword": keyword},
            )
    return {
        "keyword": keyword,
        "matched_contact_ids": [str(contact.id) for contact in matched_contacts],
        "matched_consent_count": len(consents),
        "applied": applied,
        "number_suppressed": (
            suppression.is_suppressed if suppression is not None else None
        ),
    }


def consent_authorizes_sms(
    *,
    consent: LeadChannelConsent | None,
    to_number: str,
    category: str,
    now: datetime | None = None,
) -> bool:
    """Evaluate the complete provenance/category grant; absence fails closed."""
    checked_at = now or datetime.now(timezone.utc)
    return bool(
        consent
        and consent.sms_allowed
        and consent.sms_status == "active"
        and consent.phone_verified
        and consent.mobile_e164 == to_number
        and not consent.revoked_at
        and not consent.sms_revoked_at
        and consent.consented_at
        and consent.consent_source
        and consent.disclosure_version
        and (not consent.consent_expires_at or consent.consent_expires_at > checked_at)
        and isinstance(consent.allowed_categories, list)
        and category in consent.allowed_categories
    )


def _request_digest(*, contact_id, matter_id, to_number, body, category) -> str:
    canonical = json.dumps(
        {
            "contact_id": str(contact_id),
            "matter_id": str(matter_id) if matter_id else None,
            "to_number": to_number,
            "body": body,
            "category": category,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _communication(
    *, tenant_id, message: SmsMessage, user_id=None, status: str
) -> CommunicationLog:
    return CommunicationLog(
        tenant_id=tenant_id,
        contact_id=message.contact_id,
        matter_id=message.matter_id,
        created_by_user_id=user_id,
        direction=message.direction,
        channel="sms",
        status=status,
        subject="SMS",
        body=message.body,
        external_ref=(
            f"sms:{message.provider_message_id}"
            if message.provider_message_id
            else f"sms-local:{message.id}"
        ),
        participants={"from": message.from_number, "to": [message.to_number]},
    )


def _record_communication(
    db: AsyncSession, *, tenant_id, message: SmsMessage, user_id=None, status: str
) -> None:
    log = _communication(
        tenant_id=tenant_id, message=message, user_id=user_id, status=status
    )
    if log.id is None:
        log.id = uuid.uuid4()
    message.communication_log_id = log.id
    db.add(log)


async def _lock_sms_automation_run(
    db: AsyncSession, *, tenant_id, message: SmsMessage
) -> TaskAutomationRun | None:
    """Bind a run even when a crash preceded its SmsMessage foreign key write."""
    bindings = [
        TaskAutomationRun.sms_message_id == message.id,
        and_(
            TaskAutomationRun.sms_message_id.is_(None),
            TaskAutomationRun.action_snapshot["idempotency_key"].as_string()
            == message.idempotency_key,
        ),
    ]
    if message.provider_message_id:
        bindings.append(
            TaskAutomationRun.provider_message_id == message.provider_message_id
        )
    return await db.scalar(
        select(TaskAutomationRun)
        .where(
            TaskAutomationRun.tenant_id == tenant_id,
            TaskAutomationRun.action_type == "sms_client",
            or_(*bindings),
        )
        .order_by(TaskAutomationRun.created_at.desc(), TaskAutomationRun.id.desc())
        .limit(1)
        .with_for_update()
    )


async def _lock_sms_timeline_targets(
    db: AsyncSession,
    *,
    tenant_id,
    message: SmsMessage,
    include_matter: bool,
) -> bool:
    """Fence timeline foreign-key targets in Contact-before-Matter order."""

    if message.contact_id is not None:
        contact_id = await db.scalar(
            select(Contact.id)
            .where(
                Contact.id == message.contact_id,
                Contact.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if contact_id is None:
            return False
    if include_matter and message.matter_id is not None:
        matter_id = await db.scalar(
            select(Matter.id)
            .where(Matter.id == message.matter_id, Matter.tenant_id == tenant_id)
            .with_for_update(of=Matter)
        )
        if matter_id is None:
            return False
    return True


async def _ensure_unknown_delivery_evidence(
    db: AsyncSession,
    *,
    tenant_id,
    message: SmsMessage,
    actor_user_id,
    reason: str,
    audit_actor_type: str | None = None,
) -> None:
    """Atomically retain one timeline row and one audit marker per unknown cause.

    Callers must already fence the message's Contact before Matter. A missing
    CommunicationLog insert checks both foreign keys, and relying on database
    constraint order can otherwise invert inbound STOP's Contact -> Matter
    routing order.
    """
    communication = None
    if message.communication_log_id:
        communication = await db.scalar(
            select(CommunicationLog).where(
                CommunicationLog.tenant_id == tenant_id,
                CommunicationLog.id == message.communication_log_id,
            )
        )
    if communication is None:
        _record_communication(
            db,
            tenant_id=tenant_id,
            message=message,
            user_id=actor_user_id,
            status="unknown",
        )
    else:
        communication.status = "unknown"

    automation_run = await _lock_sms_automation_run(
        db, tenant_id=tenant_id, message=message
    )
    if automation_run:
        detail = "SMS dispatch outcome is unknown and requires reconciliation"
        automation_run.sms_message_id = message.id
        automation_run.status = "failed"
        automation_run.delivery_certainty = "outcome_unknown"
        automation_run.reconciliation_required = True
        automation_run.error_message = detail
        automation_run.delivery_detail = detail
        automation_run.provider = "twilio"
        automation_run.provider_message_id = (
            message.provider_message_id or automation_run.provider_message_id
        )
        automation_run.completed_at = automation_run.completed_at or datetime.now(
            timezone.utc
        )

    raw_event = dict(message.raw_provider_event or {})
    evidence_reasons = list(raw_event.get("unknown_evidence_reasons") or [])
    if reason not in evidence_reasons:
        _append_sms_outcome_audit(
            db,
            message=message,
            actor_user_id=actor_user_id,
            outcome="outcome_unknown",
            metadata={"reason": reason},
            actor_type=audit_actor_type,
        )
        evidence_reasons.append(reason)
        raw_event["unknown_evidence_reasons"] = evidence_reasons
        message.raw_provider_event = raw_event


def _terminal_replay(message: SmsMessage) -> SmsMessage:
    """Return only provider-accepted truth; preserve failed attempts as failures."""
    if message.status in {"submitted", "delivered"}:
        return message
    if message.status == "provider_failed":
        raise SmsError(
            "The SMS provider rejected the original request",
            409,
            delivery_certainty="provider_rejected",
            sms_message_id=message.id,
        )
    if message.status == "provider_failed_after_acceptance":
        raise SmsError(
            "The provider accepted the original SMS before delivery failed; create a new reviewed request",
            409,
            delivery_certainty="provider_failed_after_acceptance",
            sms_message_id=message.id,
        )
    if message.status in {
        "blocked_consent_changed",
        "blocked_number_suppression",
        "blocked_provider_config",
        "blocked_quiet_hours",
        "blocked_matter_authorization_changed",
    }:
        raise SmsError(
            "The original SMS was blocked before provider dispatch",
            409,
            delivery_certainty="not_attempted",
            sms_message_id=message.id,
        )
    raise SmsError(
        "The original SMS does not have provider-accepted delivery truth",
        409,
        delivery_certainty="outcome_unknown",
        sms_message_id=message.id,
    )


async def _resolve_replay(
    db: AsyncSession,
    *,
    tenant_id,
    replay: SmsMessage,
    request_digest: str,
    now: datetime,
    actor_user_id,
) -> SmsMessage:
    if replay.request_digest != request_digest:
        raise SmsError("Idempotency key was reused for a different SMS", 409)
    if replay.status == "dispatching":
        lease_started = replay.dispatch_started_at
        if lease_started and lease_started.tzinfo is None:
            lease_started = lease_started.replace(tzinfo=timezone.utc)
        if lease_started and lease_started <= now - _DISPATCH_LEASE:
            # Every caller owns this SmsMessage row before Contact/Matter, so
            # do not reacquire it here and invert recovery/scheduler ordering.
            replay.status = "provider_unknown"
            replay.delivery_certainty = "outcome_unknown"
            replay.reconciliation_required_at = now
            replay.raw_provider_event = {
                **(replay.raw_provider_event or {}),
                "reconciliation_reason": "dispatch_lease_expired",
            }
            await _ensure_unknown_delivery_evidence(
                db,
                tenant_id=tenant_id,
                message=replay,
                actor_user_id=actor_user_id,
                reason="dispatch_lease_expired_replay",
            )
            await db.commit()
            raise SmsError(
                "The original SMS dispatch lease expired with an unknown outcome",
                409,
                delivery_certainty="outcome_unknown",
                sms_message_id=replay.id,
                reconciliation_required=True,
            )
        raise SmsError(
            "The original SMS is already being dispatched",
            409,
            delivery_certainty="outcome_unknown",
            sms_message_id=replay.id,
        )
    if replay.status == "provider_unknown" or (
        replay.reconciliation_required_at and not replay.reconciliation_resolved_at
    ):
        raise SmsError(
            "The original SMS outcome must be reconciled before retrying",
            409,
            delivery_certainty="outcome_unknown",
            sms_message_id=replay.id,
            reconciliation_required=True,
        )
    return _terminal_replay(replay)


async def _persist_unknown_dispatch(
    db: AsyncSession,
    *,
    tenant_id,
    message_id,
    attempt_id,
    user_id,
    provider_account_sid: str,
    provider_config_generation: int,
    provider_credential_id: uuid.UUID,
    provider_messaging_service_sid: str | None = None,
    provider_message_id: str | None = None,
    provider_status: str | None = None,
    provider_from_number: str | None = None,
    provider_created_at: datetime | None = None,
    provider_submission_started_at: datetime | None = None,
    failure_type: str,
) -> None:
    """Recover the durable reservation after any uncertain provider boundary."""
    try:
        await db.rollback()
        await set_tenant_context(db, str(tenant_id))
        message = await db.scalar(
            select(SmsMessage)
            .where(
                SmsMessage.id == message_id,
                SmsMessage.tenant_id == tenant_id,
                SmsMessage.dispatch_attempt_id == attempt_id,
            )
            .with_for_update()
        )
        if message and message.status == "dispatching":
            if not await _lock_sms_timeline_targets(
                db,
                tenant_id=tenant_id,
                message=message,
                include_matter=True,
            ):
                raise SmsError(
                    "SMS timeline target is unavailable",
                    409,
                    sms_message_id=message.id,
                    code="sms_timeline_target_unavailable",
                )
            message.status = "provider_unknown"
            message.delivery_certainty = "outcome_unknown"
            message.provider_account_sid = provider_account_sid
            message.provider_messaging_service_sid = provider_messaging_service_sid
            message.provider_config_generation = provider_config_generation
            message.provider_credential_id = provider_credential_id
            message.provider_message_id = provider_message_id
            message.provider_status = provider_status
            message.from_number = provider_from_number or message.from_number
            message.provider_created_at = provider_created_at
            message.provider_submission_started_at = (
                provider_submission_started_at or message.provider_submission_started_at
            )
            message.provider_error_code = failure_type
            message.reconciliation_required_at = datetime.now(timezone.utc)
            message.raw_provider_event = {
                **(message.raw_provider_event or {}),
                "failure": failure_type,
            }
            await _ensure_unknown_delivery_evidence(
                db,
                tenant_id=tenant_id,
                message=message,
                actor_user_id=user_id,
                reason=f"provider_boundary:{failure_type}",
            )
        await db.commit()
    except Exception:
        # The pre-provider reservation was committed before dispatch. If the
        # database is unavailable here, the bounded stale-lease reconciler will
        # still fence that exact row as provider_unknown after recovery.
        await db.rollback()


async def send_sms(
    db: AsyncSession,
    *,
    tenant_id,
    user_id,
    contact_id,
    matter_id,
    body: str,
    category: str,
    idempotency_key: str,
    required_capabilities: frozenset[str] = frozenset({"manage_matters"}),
    before_success_commit: Callable[[SmsMessage], Awaitable[None]] | None = None,
) -> SmsMessage:
    now = datetime.now(timezone.utc)
    if matter_id is None:
        raise SmsError(
            "A matter-bound SMS target is required",
            422,
            code="sms_matter_required",
        )
    # Provider configuration updates take this fence before they write actor
    # foreign keys. Send must therefore take it before every actor/role/Matter
    # lock too, including replay, or rotation and send can form an
    # advisory-lock <-> actor-row deadlock.
    await lock_provider_config_admission(db, tenant_id=tenant_id)
    # Authorize before even interpreting a matching idempotency reservation.
    # A caller who lost capability, matter assignment, or party binding must
    # not learn whether a prior provider request exists or how it resolved.
    # This first pass must remain non-locking: inbound STOP takes Contact before
    # Matter, and a new reservation's Contact FK may wait behind that STOP.
    if not await _sms_matter_authorization_preflight(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        matter_id=matter_id,
        contact_id=contact_id,
        required_capabilities=required_capabilities,
    ):
        raise SmsError(
            "SMS target was not found",
            404,
            code="sms_target_not_found",
        )
    replay_id = await db.scalar(
        select(SmsMessage.id).where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.idempotency_key == idempotency_key,
        )
    )
    if replay_id:
        # The non-locking preflight protects secrecy but cannot authorize a
        # replay across a concurrent deactivation, role revocation, assignment
        # removal, or party unlink. Lock the outbound row first to match every
        # recovery/reconciliation path, then Contact/consent before actor and
        # Matter. No replay field is interpreted until that fence succeeds.
        replay = await _lock_sms_replay(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            matter_id=matter_id,
            contact_id=contact_id,
            idempotency_key=idempotency_key,
            required_capabilities=required_capabilities,
        )
        if replay is None:
            raise SmsError(
                "SMS idempotency reservation is unavailable",
                409,
                code="sms_idempotency_reservation_unavailable",
            )
        replay_digest = _request_digest(
            contact_id=contact_id,
            matter_id=matter_id,
            to_number=replay.to_number,
            body=body,
            category=category,
        )
        resolved = await _resolve_replay(
            db,
            tenant_id=tenant_id,
            replay=replay,
            request_digest=replay_digest,
            now=now,
            actor_user_id=user_id,
        )
        if before_success_commit:
            await before_success_commit(resolved)
            await db.commit()
        return resolved
    contact = await db.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
    )
    if not contact or not contact.is_active:
        raise SmsError("SMS target was not found", 404, code="sms_target_not_found")
    to_number = normalize_e164(contact.phone)
    consent = await load_sms_consent(db, tenant_id, contact.id)
    if not consent_authorizes_sms(
        consent=consent, to_number=to_number, category=category, now=now
    ):
        raise SmsError("SMS follow-up is not currently consented", 403)
    initial_config = await _config(db, tenant_id)
    provider_auth_token(initial_config)
    initial_credential = await ensure_provider_config_credential(
        db, config=initial_config
    )
    initial_from_number = str(initial_config.from_number or "").strip() or None
    if initial_from_number:
        initial_from_number = normalize_e164(initial_from_number)
    request_digest = _request_digest(
        contact_id=contact.id,
        matter_id=matter_id,
        to_number=to_number,
        body=body,
        category=category,
    )
    attempt_id = uuid.uuid4()
    message = SmsMessage(
        tenant_id=tenant_id,
        contact_id=contact.id,
        matter_id=matter_id,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        direction="outbound",
        status="queued",
        delivery_certainty="not_attempted",
        dispatch_attempt_id=attempt_id,
        dispatch_started_at=now,
        to_number=to_number,
        body=body,
        category=category,
        created_by_user_id=user_id,
        provider_account_sid=str(initial_config.account_sid).strip(),
        provider_messaging_service_sid=(
            str(initial_config.messaging_service_sid or "").strip() or None
        ),
        provider_config_generation=initial_config.generation,
        provider_credential_id=initial_credential.id,
        from_number=initial_from_number,
    )
    db.add(message)
    observed_provider_message_id = None
    observed_provider_status = None
    observed_provider_from_number = None
    observed_provider_created_at = None
    provider_submission_started_at = None
    messaging_service_sid = None
    provider_credential_id = initial_credential.id
    dispatch_message_id = None
    try:
        await db.flush()
        dispatch_message_id = message.id
        # Reserve the idempotency key before any provider request. A concurrent
        # caller waits on the tenant/key unique constraint and then replays or
        # receives a reconciliation-required response, never a second dispatch.
        message.status = "dispatching"
        message.delivery_certainty = "outcome_unknown"
        await db.commit()
        await set_tenant_context(db, str(tenant_id))
    except IntegrityError:
        await db.rollback()
        await set_tenant_context(db, str(tenant_id))
        # The unique-key winner is replay truth too. Lock it first, then repeat
        # the same authorization fence before inspecting digest, status, or id.
        replay = await _lock_sms_replay(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            matter_id=matter_id,
            contact_id=contact_id,
            idempotency_key=idempotency_key,
            required_capabilities=required_capabilities,
        )
        if replay and replay.request_digest == request_digest:
            if replay.status == "dispatching" or (
                replay.status == "provider_unknown"
                and not replay.reconciliation_resolved_at
            ):
                raise SmsError(
                    "The original SMS is already being dispatched or requires reconciliation",
                    409,
                    delivery_certainty="outcome_unknown",
                    sms_message_id=replay.id,
                    reconciliation_required=replay.status == "provider_unknown",
                )
            replay = _terminal_replay(replay)
            if before_success_commit:
                await before_success_commit(replay)
                await db.commit()
            return replay
        raise SmsError("SMS idempotency reservation failed", 409)
    # The reservation is durable before dispatch. STOP and send first share the
    # tenant/number fence, then take contact/consent locks in the same order, so
    # exactly one ordering wins even when STOP cannot resolve an identity.
    suppression = await lock_sms_number_suppression(
        db,
        tenant_id=tenant_id,
        mobile_e164=to_number,
        initial_suppressed=False,
    )
    message = await db.scalar(
        select(SmsMessage)
        .where(
            SmsMessage.id == dispatch_message_id,
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.dispatch_attempt_id == attempt_id,
            SmsMessage.status == "dispatching",
        )
        .with_for_update()
    )
    if message is None:
        raise SmsError(
            "SMS dispatch ownership changed before provider submission",
            409,
            delivery_certainty="outcome_unknown",
            sms_message_id=dispatch_message_id,
            reconciliation_required=True,
            code="sms_dispatch_ownership_changed",
        )
    if suppression.is_suppressed:
        message.status = "blocked_number_suppression"
        message.delivery_certainty = "not_attempted"
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        _append_sms_outcome_audit(
            db,
            message=message,
            actor_user_id=user_id,
            outcome="blocked",
            metadata={"reason": "number_suppression"},
        )
        await db.commit()
        raise SmsError(
            "SMS destination has a durable provider opt-out",
            409,
            delivery_certainty="not_attempted",
            sms_message_id=dispatch_message_id,
        )
    locked_contact = await db.scalar(
        select(Contact)
        .where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
        .with_for_update()
    )
    locked_consent = (
        await load_sms_consent(db, tenant_id, contact_id, lock=True)
        if locked_contact
        else None
    )
    locked_now = datetime.now(timezone.utc)
    try:
        locked_number = normalize_e164(locked_contact.phone if locked_contact else None)
    except SmsError:
        locked_number = ""
    if (
        not locked_contact
        or not locked_contact.is_active
        or locked_number != to_number
        or not consent_authorizes_sms(
            consent=locked_consent,
            to_number=to_number,
            category=category,
            now=locked_now,
        )
    ):
        message.status = "blocked_consent_changed"
        message.delivery_certainty = "not_attempted"
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        _append_sms_outcome_audit(
            db,
            message=message,
            actor_user_id=user_id,
            outcome="blocked",
            metadata={"reason": "consent_changed"},
        )
        await db.commit()
        raise SmsError(
            "SMS consent changed before provider dispatch",
            409,
            delivery_certainty="not_attempted",
            sms_message_id=dispatch_message_id,
            code="sms_consent_changed",
        )
    if in_quiet_hours(consent=locked_consent, now=locked_now):
        message.status = "blocked_quiet_hours"
        message.delivery_certainty = "not_attempted"
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        _append_sms_outcome_audit(
            db,
            message=message,
            actor_user_id=user_id,
            outcome="blocked",
            metadata={"reason": "quiet_hours"},
        )
        await db.commit()
        raise SmsError(
            "SMS is blocked during the recipient's quiet hours",
            409,
            delivery_certainty="not_attempted",
            sms_message_id=dispatch_message_id,
            code="sms_quiet_hours",
        )
    try:
        # Re-read and share-lock the active generation immediately before
        # submission. Concurrent sends remain compatible, while an admin
        # rotation/deactivation waits until this exact outcome is durable.
        config = await _config(db, tenant_id, lock_for_provider_io=True)
        auth_token = provider_auth_token(config)
        credential = await ensure_provider_config_credential(db, config=config)
        provider_config_generation = config.generation
        provider_credential_id = credential.id
        account_sid = str(config.account_sid).strip()
        messaging_service_sid = str(config.messaging_service_sid or "").strip() or None
        from_number = str(config.from_number or "").strip() or None
        if from_number:
            from_number = normalize_e164(from_number)
        message.provider_config_generation = provider_config_generation
        message.provider_credential_id = provider_credential_id
        message.provider_account_sid = account_sid
        message.provider_messaging_service_sid = messaging_service_sid
        message.from_number = from_number
    except SmsError as exc:
        message.status = "blocked_provider_config"
        message.delivery_certainty = "not_attempted"
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        _append_sms_outcome_audit(
            db,
            message=message,
            actor_user_id=user_id,
            outcome="blocked",
            metadata={"reason": "provider_config"},
        )
        await db.commit()
        raise SmsError(
            str(exc),
            exc.status_code,
            delivery_certainty="not_attempted",
            sms_message_id=dispatch_message_id,
        ) from exc
    final_authorization = await _lock_sms_matter_authorization(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        matter_id=matter_id,
        contact_id=contact_id,
        required_capabilities=required_capabilities,
    )
    if final_authorization is None:
        message.status = "blocked_matter_authorization_changed"
        message.delivery_certainty = "not_attempted"
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        _append_sms_outcome_audit(
            db,
            message=message,
            actor_user_id=user_id,
            outcome="blocked",
            metadata={"reason": "matter_authorization_changed"},
        )
        await db.commit()
        raise SmsError(
            "SMS matter authorization changed before provider dispatch",
            409,
            delivery_certainty="not_attempted",
            sms_message_id=dispatch_message_id,
            code="sms_matter_authorization_changed",
        )
    message.raw_provider_event = {
        **(message.raw_provider_event or {}),
        "authorization": final_authorization,
    }
    try:
        data = {"To": to_number, "Body": body}
        if messaging_service_sid:
            data["MessagingServiceSid"] = messaging_service_sid
        else:
            data["From"] = from_number or ""
        provider_submission_started_at = datetime.now(timezone.utc)
        message.provider_submission_started_at = provider_submission_started_at
        async with httpx.AsyncClient(timeout=15) as http:
            response = await http.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                data=data,
                auth=(account_sid, auth_token),
            )
        payload = response.json() if response.content else {}
        provider_status = str(payload.get("status") or "").lower()
        observed_provider_message_id = (
            str(payload["sid"]) if payload.get("sid") else None
        )
        observed_provider_status = provider_status or None
        observed_provider_from_number = (
            normalize_e164(payload.get("from")) if payload.get("from") else from_number
        )
        observed_provider_created_at = _parse_provider_datetime(
            payload.get("date_created")
        )
        if response.status_code >= 500 or response.status_code in (
            _TRANSIENT_PROVIDER_HTTP_CODES
        ):
            raise RuntimeError("provider server response did not prove rejection")
        if observed_provider_message_id and provider_status in {
            "failed",
            "undelivered",
        }:
            message.provider_message_id = observed_provider_message_id
            message.provider_status = provider_status
            message.provider_error_code = str(
                payload.get("error_code") or payload.get("code") or "delivery_failed"
            )
            message.provider_created_at = observed_provider_created_at
            message.from_number = observed_provider_from_number
            message.status = "provider_failed_after_acceptance"
            message.delivery_certainty = "provider_failed_after_acceptance"
            message.raw_provider_event = {
                **(message.raw_provider_event or {}),
                "status": provider_status,
                "status_code": response.status_code,
            }
            _record_communication(
                db,
                tenant_id=tenant_id,
                message=message,
                user_id=user_id,
                status="failed",
            )
            _append_sms_outcome_audit(
                db,
                message=message,
                actor_user_id=user_id,
                outcome="failed_after_acceptance",
            )
            await db.commit()
            raise SmsError(
                "SMS delivery failed after provider acceptance",
                503,
                delivery_certainty="provider_failed_after_acceptance",
                sms_message_id=message.id,
                code="sms_provider_failed_after_acceptance",
            )
        if observed_provider_message_id and (
            response.status_code in _DETERMINISTIC_PROVIDER_REJECTION_CODES
            or provider_status not in _KNOWN_PROVIDER_STATUSES
        ):
            raise RuntimeError("provider response with an accepted id was ambiguous")
        if response.status_code in _DETERMINISTIC_PROVIDER_REJECTION_CODES:
            message.status = "provider_failed"
            message.delivery_certainty = "provider_rejected"
            message.provider_status = provider_status or None
            message.provider_error_code = str(
                payload.get("code") or "provider_rejected"
            )
            message.raw_provider_event = {"status_code": response.status_code}
            _record_communication(
                db,
                tenant_id=tenant_id,
                message=message,
                user_id=user_id,
                status="failed",
            )
            _append_sms_outcome_audit(
                db,
                message=message,
                actor_user_id=user_id,
                outcome="rejected",
            )
            await db.commit()
            raise SmsError(
                "SMS provider did not accept the message",
                503,
                delivery_certainty="provider_rejected",
                sms_message_id=message.id,
            )
        if not payload.get("sid"):
            raise RuntimeError("provider response did not identify the submission")
        message.provider_message_id = str(payload["sid"])
        message.provider_status = provider_status or "queued"
        message.status = "submitted"
        message.delivery_certainty = "provider_accepted"
        message.from_number = observed_provider_from_number
        message.provider_created_at = observed_provider_created_at
        message.raw_provider_event = {
            **(message.raw_provider_event or {}),
            "status": message.provider_status,
        }
        _record_communication(
            db,
            tenant_id=tenant_id,
            message=message,
            user_id=user_id,
            status="submitted",
        )
        if before_success_commit:
            await before_success_commit(message)
        _append_sms_outcome_audit(
            db,
            message=message,
            actor_user_id=user_id,
            outcome="submitted",
        )
        await db.commit()
        return message
    except SmsError:
        raise
    except Exception as exc:
        await _persist_unknown_dispatch(
            db,
            tenant_id=tenant_id,
            message_id=dispatch_message_id,
            attempt_id=attempt_id,
            user_id=user_id,
            provider_account_sid=account_sid,
            provider_config_generation=provider_config_generation,
            provider_credential_id=provider_credential_id,
            provider_messaging_service_sid=messaging_service_sid,
            provider_message_id=observed_provider_message_id,
            provider_status=observed_provider_status,
            provider_from_number=observed_provider_from_number,
            provider_created_at=observed_provider_created_at,
            provider_submission_started_at=provider_submission_started_at,
            failure_type=type(exc).__name__,
        )
        raise SmsError(
            "SMS provider outcome is uncertain and requires reconciliation",
            503,
            delivery_certainty="outcome_unknown",
            sms_message_id=dispatch_message_id,
            reconciliation_required=True,
        ) from exc


async def apply_inbound(
    db: AsyncSession, *, tenant_id, params: dict[str, str], provider_message_id: str
) -> SmsMessage:
    replay = await db.scalar(
        select(SmsMessage)
        .where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.provider_message_id == provider_message_id,
        )
        .with_for_update()
    )
    if replay:
        return replay
    from_number = normalize_e164(params.get("From"))
    to_number = params.get("To")
    body = (params.get("Body") or "").strip()[:1600]
    token = body.upper().split()[0] if body else ""
    compliance = None
    if token in {
        "STOP",
        "UNSUBSCRIBE",
        "CANCEL",
        "END",
        "QUIT",
        "START",
        "UNSTOP",
        "HELP",
    }:
        # STOP/START must take the tenant/number suppression fence before any
        # Contact row. Outbound dispatch and direct compliance changes use the
        # same order, eliminating a webhook-vs-send deadlock window.
        compliance = await apply_compliance_keyword(
            db,
            tenant_id=tenant_id,
            from_number=from_number,
            keyword=token,
            provider_message_id=provider_message_id,
        )
    # Lock routing inputs in one canonical order before deriving a timeline
    # target after the optional suppression fence: Contact -> Matter ->
    # MatterParty. Contact phone is not stored as a normalized indexed value,
    # so the tenant's contact rows are deliberately locked in UUID order. This
    # also fences a null/old phone becoming the inbound number while routing is
    # decided.
    contacts = (
        await db.scalars(
            select(Contact)
            .where(Contact.tenant_id == tenant_id)
            .order_by(Contact.id)
            .with_for_update()
        )
    ).all()
    candidates = []
    for candidate in contacts:
        try:
            if normalize_e164(candidate.phone) == from_number:
                candidates.append(candidate)
        except SmsError:
            continue
    contact = candidates[0] if len(candidates) == 1 else None
    matters = []
    if candidates:
        candidate_ids = [candidate.id for candidate in candidates]
        client_matter_ids = set(
            await db.scalars(
                select(Matter.id).where(
                    Matter.tenant_id == tenant_id,
                    Matter.client_contact_id.in_(candidate_ids),
                )
            )
        )
        party_matter_ids = set(
            await db.scalars(
                select(MatterParty.matter_id).where(
                    MatterParty.tenant_id == tenant_id,
                    MatterParty.contact_id.in_(candidate_ids),
                )
            )
        )
        matter_ids = client_matter_ids | party_matter_ids
        if matter_ids:
            locked_matters = (
                await db.scalars(
                    select(Matter)
                    .where(Matter.tenant_id == tenant_id, Matter.id.in_(matter_ids))
                    .order_by(Matter.id)
                    .with_for_update(of=Matter)
                )
            ).all()
            locked_parties = (
                await db.scalars(
                    select(MatterParty)
                    .where(
                        MatterParty.tenant_id == tenant_id,
                        MatterParty.contact_id.in_(candidate_ids),
                    )
                    .order_by(MatterParty.matter_id, MatterParty.id)
                    .with_for_update(of=MatterParty)
                )
            ).all()
            final_matter_ids = {
                row.id
                for row in locked_matters
                if row.client_contact_id in candidate_ids
            } | {
                row.matter_id
                for row in locked_parties
                if row.contact_id in candidate_ids
            }
            matters = [row for row in locked_matters if row.id in final_matter_ids]
    matter = matters[0] if contact and len(matters) == 1 else None
    message = SmsMessage(
        tenant_id=tenant_id,
        contact_id=contact.id if contact else None,
        matter_id=matter.id if matter else None,
        idempotency_key=f"provider:{provider_message_id}",
        request_digest=_request_digest(
            contact_id=contact.id if contact else None,
            matter_id=matter.id if matter else None,
            to_number=to_number,
            body=body,
            category="customer_reply",
        ),
        provider_message_id=provider_message_id,
        direction="inbound",
        status="received" if contact and matter else "review_required",
        delivery_certainty="confirmed_received",
        from_number=from_number,
        to_number=to_number,
        body=body,
        category="customer_reply",
        provider_status="received",
        raw_provider_event={
            "provider_message_id": provider_message_id,
            **({"compliance": compliance} if compliance is not None else {}),
        },
    )
    db.add(message)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        await set_tenant_context(db, str(tenant_id))
        replay = await db.scalar(
            select(SmsMessage).where(
                SmsMessage.tenant_id == tenant_id,
                SmsMessage.provider_message_id == provider_message_id,
            )
        )
        if replay:
            return replay
        raise SmsError("Inbound SMS replay could not be reconciled", 409)
    if not (contact and matter):
        db.add(
            SmsReviewItem(
                tenant_id=tenant_id,
                sms_message_id=message.id,
                reason="ambiguous_inbound_route"
                if candidates or matters
                else "unmatched_inbound_route",
                candidate_contact_ids=[str(row.id) for row in candidates],
                candidate_matter_ids=[str(row.id) for row in matters],
            )
        )
    if contact and matter:
        _record_communication(
            db, tenant_id=tenant_id, message=message, status="received"
        )
    await db.commit()
    return message


def _review_candidate_ids(
    item: SmsReviewItem,
) -> tuple[set[uuid.UUID], list[uuid.UUID]] | None:
    try:
        contact_ids = {
            uuid.UUID(str(value)) for value in (item.candidate_contact_ids or [])
        }
        matter_ids = sorted(
            {uuid.UUID(str(value)) for value in (item.candidate_matter_ids or [])},
            key=str,
        )
    except (TypeError, ValueError, AttributeError):
        return None
    return contact_ids, matter_ids


async def _can_review_all_candidates(
    db: AsyncSession,
    *,
    tenant_id,
    reviewer_user_id,
    candidate_matter_ids: list[uuid.UUID],
) -> bool:
    """Perform a nonlocking, all-candidate visibility preflight."""
    reviewer = await db.scalar(
        select(User).where(
            User.id == reviewer_user_id,
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )
    if reviewer is None:
        return False
    capabilities = await get_user_capabilities(db, reviewer.id)
    if "manage_matters" not in capabilities:
        return False
    if not candidate_matter_ids:
        return reviewer.role == "admin"
    for candidate_matter_id in candidate_matter_ids:
        if not await can_access_matter(
            db,
            tenant_id=tenant_id,
            user_id=reviewer.id,
            is_admin=reviewer.role == "admin",
            matter_id=candidate_matter_id,
        ):
            return False
    return True


async def resolve_review_item(
    db: AsyncSession,
    *,
    tenant_id,
    reviewer_user_id,
    review_item_id,
    decision: str,
    contact_id=None,
    matter_id=None,
) -> SmsReviewItem:
    """Resolve an ambiguous inbound without exposing it on the matter timeline early."""
    # Read only enough immutable routing evidence to prove all-candidate access.
    # No hidden review, message, contact, actor, or matter row is locked until
    # this non-enumerating preflight succeeds.
    preflight = (
        await db.execute(
            select(SmsReviewItem, SmsMessage)
            .join(
                SmsMessage,
                (SmsMessage.tenant_id == SmsReviewItem.tenant_id)
                & (SmsMessage.id == SmsReviewItem.sms_message_id),
            )
            .where(
                SmsReviewItem.id == review_item_id,
                SmsReviewItem.tenant_id == tenant_id,
            )
        )
    ).one_or_none()
    if preflight is None or preflight[1].direction != "inbound":
        raise SmsError("SMS review item was not found", 404)
    preflight_candidates = _review_candidate_ids(preflight[0])
    if preflight_candidates is None:
        raise SmsError("SMS review item was not found", 404)
    if not await _can_review_all_candidates(
        db,
        tenant_id=tenant_id,
        reviewer_user_id=reviewer_user_id,
        candidate_matter_ids=preflight_candidates[1],
    ):
        raise SmsError("SMS review item was not found", 404)

    item = await db.scalar(
        select(SmsReviewItem)
        .where(
            SmsReviewItem.id == review_item_id,
            SmsReviewItem.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if item is None:
        raise SmsError("SMS review item was not found", 404)
    message = await db.scalar(
        select(SmsMessage)
        .where(
            SmsMessage.id == item.sms_message_id,
            SmsMessage.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if message is None or message.direction != "inbound":
        raise SmsError("Inbound SMS evidence was not found", 404)
    locked_candidates = _review_candidate_ids(item)
    if locked_candidates is None or locked_candidates != preflight_candidates:
        raise SmsError("SMS review item was not found", 404)
    if item.status != "pending":
        raise SmsError("SMS review item was already resolved", 409)
    now = datetime.now(timezone.utc)
    candidate_contact_ids, candidate_matter_ids = locked_candidates
    if decision not in {"resolve", "reject"}:
        raise SmsError("Unsupported SMS review decision", 422)
    contact = None
    if decision == "resolve":
        if not contact_id or not matter_id:
            raise SmsError("Resolution requires one contact and matter", 422)
        if (
            uuid.UUID(str(contact_id)) not in candidate_contact_ids
            or uuid.UUID(str(matter_id)) not in candidate_matter_ids
        ):
            raise SmsError("Resolution target is not a stored route candidate", 409)
        # Match outbound dispatch and inbound routing: once the review/message
        # evidence is fenced, take the target Contact before actor/matter locks.
        # Dispatch takes Contact before its final actor/matter fence, so review
        # must use the same order or the two paths can deadlock deterministically.
        contact = await db.scalar(
            select(Contact)
            .where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
            .with_for_update()
        )
        if contact is None:
            raise SmsError("Resolution target was not found", 404)
        try:
            current_phone = normalize_e164(contact.phone)
        except SmsError as exc:
            raise SmsError(
                "Resolution contact does not match the inbound phone", 409
            ) from exc
        if current_phone != message.from_number:
            raise SmsError("Resolution contact does not match the inbound phone", 409)
    reviewer = await _lock_sms_actor(
        db,
        tenant_id=tenant_id,
        user_id=reviewer_user_id,
        required_capabilities=frozenset({"manage_matters"}),
    )
    if reviewer is None:
        raise SmsError("SMS review item was not found", 404)
    if not candidate_matter_ids and reviewer.role != "admin":
        raise SmsError("SMS review item was not found", 404)
    for candidate_matter_id in candidate_matter_ids:
        if (
            await _lock_sms_matter_access(
                db,
                tenant_id=tenant_id,
                user=reviewer,
                matter_id=candidate_matter_id,
            )
            is None
        ):
            raise SmsError("SMS review item was not found", 404)
    if decision == "reject":
        item.status = "rejected"
        item.reviewed_by_user_id = reviewer_user_id
        item.reviewed_at = now
        message.status = "route_rejected"
        return item
    if (
        await _lock_sms_matter_authorization(
            db,
            tenant_id=tenant_id,
            user_id=reviewer_user_id,
            matter_id=matter_id,
            contact_id=contact_id,
            required_capabilities=frozenset({"manage_matters"}),
        )
        is None
    ):
        raise SmsError("SMS review item was not found", 404)
    message.contact_id = contact_id
    message.matter_id = matter_id
    message.status = "received"
    item.status = "resolved"
    item.reviewed_by_user_id = reviewer_user_id
    item.reviewed_at = now
    if message.communication_log_id is None:
        _record_communication(
            db, tenant_id=tenant_id, message=message, status="received"
        )
    return item


async def reconcile_sms_message(
    db: AsyncSession,
    *,
    tenant_id,
    operator_user_id,
    sms_message_id,
    resolution: str,
    provider_message_id: str | None = None,
) -> SmsMessage:
    """Record operator context or resolve uncertainty with exact provider truth."""
    await db.execute(
        text(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended(:sms_reconciliation_lock, 0))"
        ),
        {
            "sms_reconciliation_lock": (
                f"lawhand:sms-reconciliation:v1:{tenant_id}:twilio"
            )
        },
    )
    message = await db.scalar(
        select(SmsMessage)
        .where(
            SmsMessage.id == sms_message_id,
            SmsMessage.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if message is None or message.direction != "outbound":
        raise SmsError("Outbound SMS was not found", 404, code="sms_message_not_found")
    if message.reconciliation_required_at is None or message.reconciliation_resolved_at:
        raise SmsError(
            "SMS does not require reconciliation",
            409,
            sms_message_id=message.id,
            code="sms_reconciliation_not_required",
        )
    if message.contact_id is None or message.matter_id is None:
        raise SmsError("Outbound SMS was not found", 404, code="sms_message_not_found")
    if not await _lock_sms_timeline_targets(
        db,
        tenant_id=tenant_id,
        message=message,
        include_matter=False,
    ):
        raise SmsError("Outbound SMS was not found", 404, code="sms_message_not_found")
    if (
        await _lock_sms_matter_authorization(
            db,
            tenant_id=tenant_id,
            user_id=operator_user_id,
            matter_id=message.matter_id,
            contact_id=message.contact_id,
            required_capabilities=frozenset({"manage_matters"}),
        )
        is None
    ):
        raise SmsError("Outbound SMS was not found", 404, code="sms_message_not_found")
    now = datetime.now(timezone.utc)
    if resolution == "operator_attested_unknown":
        if (
            message is None
            or message.direction != "outbound"
            or message.reconciliation_required_at is None
            or message.reconciliation_resolved_at is not None
        ):
            raise SmsError(
                "SMS does not require reconciliation",
                409,
                sms_message_id=sms_message_id,
                code="sms_reconciliation_not_required",
            )
        message.status = "provider_unknown"
        message.delivery_certainty = "outcome_unknown"
        message.operator_observed_absent_at = now
        message.operator_observed_absent_by_user_id = operator_user_id
        message.reconciliation_resolution = "operator_attested_unknown"
        message.raw_provider_event = {
            **(message.raw_provider_event or {}),
            "operator_attestation": "not_seen_in_provider_console",
        }
        await _ensure_unknown_delivery_evidence(
            db,
            tenant_id=tenant_id,
            message=message,
            actor_user_id=operator_user_id,
            reason="operator_attested_unknown",
        )
        automation_run = await _lock_sms_automation_run(
            db, tenant_id=tenant_id, message=message
        )
        if automation_run:
            automation_run.status = "failed"
            automation_run.delivery_certainty = "outcome_unknown"
            automation_run.reconciliation_required = True
            automation_run.error_message = (
                "An operator recorded that the message was not visible, but the "
                "provider outcome remains unverified"
            )
        return message
    if resolution == "provider_lookup":
        lookup_sid = str(
            provider_message_id or message.provider_message_id or ""
        ).strip()
        if not lookup_sid:
            raise SmsError(
                "Provider lookup requires the provider message id",
                422,
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_message_id_required",
            )
        if message.provider_message_id and message.provider_message_id != lookup_sid:
            raise SmsError(
                "Provider did not verify the reserved dispatch identity",
                409,
                delivery_certainty="outcome_unknown",
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_identity_mismatch",
            )
        if message.provider_config_generation is None:
            raise SmsError(
                "The provider credential generation is not bound to this dispatch",
                409,
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_generation_unavailable",
            )
        credential = await provider_credentials_for_generation(
            db,
            tenant_id=tenant_id,
            generation=message.provider_config_generation,
            credential_id=message.provider_credential_id,
            lock_for_provider_io=True,
        )
        account_sid = str(credential.account_sid or "").strip()
        if (
            not message.provider_account_sid
            or message.provider_account_sid != account_sid
        ):
            raise SmsError(
                "The bound provider generation cannot verify this dispatch",
                409,
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_account_mismatch",
            )
        auth_token = provider_auth_token(credential)
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                response = await http.get(
                    f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages/{lookup_sid}.json",
                    auth=(account_sid, auth_token),
                )
            payload = response.json() if response.content else {}
        except Exception as exc:
            raise SmsError(
                "Provider lookup is unavailable; the dispatch remains uncertain",
                503,
                delivery_certainty="outcome_unknown",
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_lookup_unavailable",
            ) from exc
        if response.status_code >= 500 or response.status_code in (
            _TRANSIENT_PROVIDER_HTTP_CODES
        ):
            raise SmsError(
                "Provider lookup is unavailable; the dispatch remains uncertain",
                503,
                delivery_certainty="outcome_unknown",
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_lookup_unavailable",
            )
        if response.status_code >= 400:
            raise SmsError(
                "Provider did not verify that message identity",
                409,
                delivery_certainty="outcome_unknown",
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_identity_unverified",
            )
        incoming = str(payload.get("status") or "").lower()
        if incoming not in _KNOWN_PROVIDER_STATUSES:
            raise SmsError(
                "Provider lookup returned an unsupported status",
                409,
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_status_unverified",
            )
        try:
            provider_to = normalize_e164(payload.get("to"))
            provider_from = normalize_e164(payload.get("from"))
        except SmsError as exc:
            raise SmsError(
                "Provider did not verify the reserved dispatch identity",
                409,
                delivery_certainty="outcome_unknown",
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_identity_mismatch",
            ) from exc
        provider_created_at = _parse_provider_datetime(payload.get("date_created"))
        provider_service = (
            str(payload.get("messaging_service_sid") or "").strip() or None
        )
        if provider_created_at is None:
            raise SmsError(
                "Provider did not verify the reserved dispatch identity",
                409,
                delivery_certainty="outcome_unknown",
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_identity_mismatch",
            )
        sender_condition = (
            or_(
                SmsMessage.from_number.is_(None),
                SmsMessage.from_number == provider_from,
            )
            if provider_service is not None
            else SmsMessage.from_number == provider_from
        )
        candidates = list(
            (
                await db.scalars(
                    select(SmsMessage)
                    .where(
                        SmsMessage.tenant_id == tenant_id,
                        SmsMessage.direction == "outbound",
                        SmsMessage.reconciliation_required_at.is_not(None),
                        SmsMessage.reconciliation_resolved_at.is_(None),
                        SmsMessage.provider_account_sid == account_sid,
                        SmsMessage.provider_messaging_service_sid == provider_service,
                        sender_condition,
                        SmsMessage.to_number == provider_to,
                        SmsMessage.body == str(payload.get("body") or ""),
                        SmsMessage.provider_submission_started_at
                        >= provider_created_at - _PROVIDER_CREATED_AFTER_DISPATCH,
                        SmsMessage.provider_submission_started_at
                        <= provider_created_at + _PROVIDER_CREATED_BEFORE_DISPATCH,
                        or_(
                            SmsMessage.provider_message_id.is_(None),
                            SmsMessage.provider_message_id == lookup_sid,
                        ),
                    )
                    .order_by(SmsMessage.id)
                    .with_for_update()
                )
            ).all()
        )
        exact_candidates = [
            candidate
            for candidate in candidates
            if _provider_lookup_matches_reserved_dispatch(
                message=candidate,
                payload=payload,
                lookup_sid=lookup_sid,
                account_sid=account_sid,
            )
        ]
        if len(exact_candidates) != 1 or exact_candidates[0].id != message.id:
            raise SmsError(
                "Provider record does not uniquely identify this local dispatch",
                409,
                delivery_certainty="outcome_unknown",
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_identity_ambiguous",
            )
        message = exact_candidates[0]
        duplicate = await db.scalar(
            select(SmsMessage.id)
            .where(
                SmsMessage.tenant_id == tenant_id,
                SmsMessage.provider_message_id == lookup_sid,
                SmsMessage.id != message.id,
            )
            .with_for_update()
        )
        if duplicate:
            raise SmsError(
                "Provider message id is already bound",
                409,
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_identity_conflict",
            )
        message.provider_message_id = lookup_sid
        message.provider_account_sid = account_sid
        message.provider_status = incoming
        message.provider_created_at = provider_created_at
        message.from_number = provider_from
        message.raw_provider_event = {
            **(message.raw_provider_event or {}),
            "reconciled_by": "provider_lookup",
            "status": incoming,
        }
        if incoming in {"failed", "undelivered"}:
            message.status = "provider_failed_after_acceptance"
            message.delivery_certainty = "provider_failed_after_acceptance"
            resolved_certainty = "provider_failed_after_acceptance"
            resolved_status = "failed"
            resolved_error = "SMS delivery failed after provider acceptance"
        elif incoming in {"delivered", "read"}:
            message.status = "delivered"
            message.delivery_certainty = "confirmed_sent"
            resolved_certainty = "confirmed_sent"
            resolved_status = "sent"
            resolved_error = None
        else:
            message.status = "submitted"
            message.delivery_certainty = "provider_accepted"
            resolved_certainty = "provider_accepted"
            resolved_status = "submitted"
            resolved_error = None
    else:
        raise SmsError(
            "Unsupported SMS reconciliation resolution",
            422,
            code="sms_reconciliation_resolution_invalid",
        )
    message.reconciliation_resolved_at = now
    message.reconciliation_resolved_by_user_id = operator_user_id
    message.reconciliation_resolution = resolution
    communication = None
    if message.communication_log_id:
        communication = await db.scalar(
            select(CommunicationLog).where(
                CommunicationLog.tenant_id == tenant_id,
                CommunicationLog.id == message.communication_log_id,
            )
        )
    if resolution == "provider_lookup":
        if communication:
            communication.status = (
                "delivered"
                if resolved_status == "sent"
                else ("failed" if resolved_status == "failed" else "submitted")
            )
            communication.external_ref = f"sms:{message.provider_message_id}"
        else:
            _record_communication(
                db,
                tenant_id=tenant_id,
                message=message,
                user_id=message.created_by_user_id,
                status=(
                    "delivered"
                    if resolved_status == "sent"
                    else ("failed" if resolved_status == "failed" else "submitted")
                ),
            )
    automation_run = await _lock_sms_automation_run(
        db, tenant_id=tenant_id, message=message
    )
    if automation_run:
        automation_run.reconciliation_required = False
        automation_run.provider_message_id = message.provider_message_id
        automation_run.status = resolved_status
        automation_run.delivery_certainty = resolved_certainty
        automation_run.error_message = resolved_error
    return message


async def mark_stale_sms_dispatches_for_reconciliation() -> int:
    """Bound stale leases so crashes become explicit operator work, never retries."""
    now = datetime.now(timezone.utc)
    dispatch_cutoff = now - _DISPATCH_LEASE
    submitted_cutoff = now - timedelta(minutes=30)
    async with async_session_maker() as catalog_db:
        tenant_ids = list((await catalog_db.scalars(select(Tenant.id))).all())
    changed = 0
    for tenant_id in tenant_ids:
        stale_predicate = or_(
            (
                (SmsMessage.status == "dispatching")
                & (SmsMessage.dispatch_started_at <= dispatch_cutoff)
            ),
            (
                (SmsMessage.status == "submitted")
                & (SmsMessage.created_at <= submitted_cutoff)
                & SmsMessage.reconciliation_required_at.is_(None)
            ),
        )
        # Discovery is lock-free. Claim rows one at a time in deterministic id
        # order so no transaction holds an unordered SmsMessage batch while it
        # accumulates Contact/Matter locks for timeline evidence.
        async with async_session_maker() as discovery_db:
            await set_tenant_context(discovery_db, str(tenant_id))
            candidate_ids = list(
                (
                    await discovery_db.scalars(
                        select(SmsMessage.id)
                        .where(
                            SmsMessage.tenant_id == tenant_id,
                            SmsMessage.direction == "outbound",
                            stale_predicate,
                        )
                        .order_by(SmsMessage.id)
                    )
                ).all()
            )
        for candidate_id in candidate_ids:
            async with async_session_maker() as db:
                await set_tenant_context(db, str(tenant_id))
                row = await db.scalar(
                    select(SmsMessage)
                    .where(
                        SmsMessage.id == candidate_id,
                        SmsMessage.tenant_id == tenant_id,
                        SmsMessage.direction == "outbound",
                        stale_predicate,
                    )
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    continue
                reason = (
                    "dispatch_lease_expired"
                    if row.status == "dispatching"
                    else "signed_status_callback_overdue"
                )
                if row.status == "dispatching":
                    row.status = "provider_unknown"
                    row.delivery_certainty = "outcome_unknown"
                row.reconciliation_required_at = now
                row.raw_provider_event = {
                    **(row.raw_provider_event or {}),
                    "reconciliation_reason": reason,
                }
                if row.status == "provider_unknown":
                    if not await _lock_sms_timeline_targets(
                        db,
                        tenant_id=tenant_id,
                        message=row,
                        include_matter=True,
                    ):
                        raise SmsError(
                            "SMS timeline target is unavailable",
                            409,
                            sms_message_id=row.id,
                            code="sms_timeline_target_unavailable",
                        )
                    await _ensure_unknown_delivery_evidence(
                        db,
                        tenant_id=tenant_id,
                        message=row,
                        actor_user_id=None,
                        reason=f"scheduler:{reason}",
                        audit_actor_type="system",
                    )
                await db.commit()
                changed += 1
    return changed


async def apply_status(
    db: AsyncSession, *, tenant_id, params: dict[str, str]
) -> SmsMessage:
    sid = params.get("MessageSid") or ""
    message = await db.scalar(
        select(SmsMessage)
        .where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.provider_message_id == sid,
        )
        .with_for_update()
    )
    if not message:
        raise SmsError("Unknown SMS provider message", 404)
    incoming = (params.get("MessageStatus") or "").lower()
    if provider_status_transition_allowed(
        current=message.provider_status, incoming=incoming
    ):
        message.provider_status = incoming or message.provider_status
        message.status = (
            "delivered"
            if incoming in {"delivered", "read"}
            else (
                "provider_failed_after_acceptance"
                if incoming in {"failed", "undelivered"}
                else "submitted"
            )
        )
        message.delivery_certainty = (
            "confirmed_sent"
            if incoming in {"delivered", "read"}
            else (
                "provider_failed_after_acceptance"
                if incoming in {"failed", "undelivered"}
                else "provider_accepted"
            )
        )
        message.provider_error_code = (
            params.get("ErrorCode") or message.provider_error_code
        )
        try:
            message.segment_count = (
                int(params["NumSegments"])
                if params.get("NumSegments")
                else message.segment_count
            )
        except ValueError:
            pass
        try:
            message.cost = (
                Decimal(params["Price"]) if params.get("Price") else message.cost
            )
        except (InvalidOperation, ValueError):
            pass
        message.raw_provider_event = {
            **(message.raw_provider_event or {}),
            "status": incoming,
        }
        message.last_event_at = datetime.now(timezone.utc)
        if (
            message.reconciliation_required_at
            and not message.reconciliation_resolved_at
        ):
            message.reconciliation_resolved_at = message.last_event_at
            message.reconciliation_resolution = (
                "signed_callback_overrode_operator_attestation"
                if message.operator_observed_absent_at
                else "signed_provider_callback"
            )
            message.reconciliation_resolved_by_user_id = None
        communication = None
        if message.communication_log_id:
            communication = await db.scalar(
                select(CommunicationLog).where(
                    CommunicationLog.tenant_id == tenant_id,
                    CommunicationLog.id == message.communication_log_id,
                )
            )
        if communication:
            communication.status = (
                "delivered"
                if incoming in {"delivered", "read"}
                else "failed"
                if incoming in {"failed", "undelivered"}
                else "submitted"
            )
        automation_run = await _lock_sms_automation_run(
            db, tenant_id=tenant_id, message=message
        )
        if automation_run:
            automation_run.sms_message_id = message.id
            automation_run.reconciliation_required = False
            automation_run.provider = "twilio"
            automation_run.provider_message_id = sid
            if incoming in {"delivered", "read"}:
                automation_run.status = "sent"
                automation_run.delivery_certainty = "confirmed_sent"
                automation_run.error_message = None
                automation_run.delivery_detail = (
                    "SMS delivery was confirmed by a signed provider callback."
                )
            elif incoming in {"failed", "undelivered"}:
                automation_run.status = "failed"
                automation_run.delivery_certainty = "provider_failed_after_acceptance"
                automation_run.error_message = (
                    "SMS delivery failed after provider acceptance"
                )
                automation_run.delivery_detail = automation_run.error_message
            else:
                automation_run.status = "submitted"
                automation_run.delivery_certainty = "provider_accepted"
                automation_run.error_message = None
                automation_run.delivery_detail = (
                    "SMS was accepted by the provider; delivery remains "
                    "signed-callback reconciled."
                )
        _append_sms_outcome_audit(
            db,
            message=message,
            actor_user_id=None,
            outcome="signed_status_callback",
            metadata={"provider_status": incoming},
        )
        await db.commit()
    return message
