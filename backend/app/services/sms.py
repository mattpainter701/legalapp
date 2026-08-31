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
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker, set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.conversion_loop import LeadChannelConsent, SmsConsentEvent
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.models.sms import (
    SmsMessage,
    SmsNumberSuppression,
    SmsNumberSuppressionEvent,
    SmsProviderConfig,
    SmsReviewItem,
)
from app.models.task import TaskAutomationRun
from app.models.tenant import Tenant
from app.services.token_vault import decrypt_token


_DETERMINISTIC_PROVIDER_REJECTION_CODES = frozenset({400, 401, 403, 404, 422})
_TRANSIENT_PROVIDER_HTTP_CODES = frozenset({408, 409, 425, 429})


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


def normalize_e164(value: str | None) -> str:
    raw = re.sub(r"[ ().-]", "", str(value or ""))
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if not re.fullmatch(r"\+[1-9]\d{7,14}", raw):
        raise SmsError("A verified E.164 mobile destination is required", 422)
    return raw


def twilio_signature(*, auth_token: str, url: str, params: dict[str, str]) -> str:
    canonical = url + "".join(f"{key}{params[key]}" for key in sorted(params))
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
    if normalized_current in _TERMINAL_PROVIDER_STATUSES:
        return normalized_incoming == normalized_current or (
            normalized_current == "delivered" and normalized_incoming == "read"
        )
    return _provider_status_rank(normalized_incoming) >= _provider_status_rank(
        normalized_current
    )


async def _config(db: AsyncSession, tenant_id) -> SmsProviderConfig:
    statement = (
        select(SmsProviderConfig)
        .where(
            SmsProviderConfig.tenant_id == tenant_id,
            SmsProviderConfig.provider == "twilio",
            SmsProviderConfig.is_active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
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


def provider_auth_token(config: SmsProviderConfig) -> str:
    """Decrypt and validate the Twilio Account Auth Token at each use."""
    try:
        token = decrypt_token(config.encrypted_auth_token).strip()
    except Exception as exc:
        raise SmsError("SMS provider credentials are unavailable", 503) from exc
    if not token:
        raise SmsError("SMS provider credentials are unavailable", 503)
    return token


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


def _terminal_replay(message: SmsMessage) -> SmsMessage:
    """Return only provider-accepted truth; preserve failed attempts as failures."""
    if message.status in {"submitted", "delivered"}:
        return message
    if message.status in {"provider_failed", "failed"}:
        raise SmsError(
            "The SMS provider rejected the original request",
            409,
            delivery_certainty="provider_rejected",
            sms_message_id=message.id,
        )
    if message.status == "reconciled_not_sent":
        raise SmsError(
            "The original SMS was confirmed not sent; create a new reviewed request",
            409,
            delivery_certainty="confirmed_not_sent",
            sms_message_id=message.id,
        )
    if message.status in {
        "blocked_consent_changed",
        "blocked_number_suppression",
        "blocked_provider_config",
        "blocked_quiet_hours",
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
) -> SmsMessage:
    if replay.request_digest != request_digest:
        raise SmsError("Idempotency key was reused for a different SMS", 409)
    if replay.status == "dispatching":
        lease_started = replay.dispatch_started_at
        if lease_started and lease_started.tzinfo is None:
            lease_started = lease_started.replace(tzinfo=timezone.utc)
        if lease_started and lease_started <= now - _DISPATCH_LEASE:
            locked_replay = await db.scalar(
                select(SmsMessage)
                .where(
                    SmsMessage.id == replay.id,
                    SmsMessage.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if locked_replay.status == "dispatching":
                locked_replay.status = "provider_unknown"
                locked_replay.reconciliation_required_at = now
                locked_replay.raw_provider_event = {
                    **(locked_replay.raw_provider_event or {}),
                    "reconciliation_reason": "dispatch_lease_expired",
                }
                await db.commit()
                raise SmsError(
                    "The original SMS dispatch lease expired with an unknown outcome",
                    409,
                    delivery_certainty="outcome_unknown",
                    sms_message_id=locked_replay.id,
                    reconciliation_required=True,
                )
            if locked_replay.status == "provider_unknown":
                raise SmsError(
                    "The original SMS outcome must be reconciled before retrying",
                    409,
                    delivery_certainty="outcome_unknown",
                    sms_message_id=locked_replay.id,
                    reconciliation_required=True,
                )
            return _terminal_replay(locked_replay)
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
    provider_message_id: str | None = None,
    provider_status: str | None = None,
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
            message.status = "provider_unknown"
            message.provider_account_sid = provider_account_sid
            message.provider_config_generation = provider_config_generation
            message.provider_message_id = provider_message_id
            message.provider_status = provider_status
            message.provider_error_code = failure_type
            message.reconciliation_required_at = datetime.now(timezone.utc)
            message.raw_provider_event = {"failure": failure_type}
            if message.communication_log_id is None:
                _record_communication(
                    db,
                    tenant_id=tenant_id,
                    message=message,
                    user_id=user_id,
                    status="unknown",
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
    before_success_commit: Callable[[SmsMessage], Awaitable[None]] | None = None,
) -> SmsMessage:
    now = datetime.now(timezone.utc)
    replay = await db.scalar(
        select(SmsMessage).where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.idempotency_key == idempotency_key,
        )
    )
    if replay:
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
        )
        if before_success_commit:
            await before_success_commit(resolved)
            await db.commit()
        return resolved
    contact = await db.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
    )
    if not contact or not contact.is_active:
        raise SmsError("SMS recipient was not found", 404)
    to_number = normalize_e164(contact.phone)
    consent = await load_sms_consent(db, tenant_id, contact.id)
    if not consent_authorizes_sms(
        consent=consent, to_number=to_number, category=category, now=now
    ):
        raise SmsError("SMS follow-up is not currently consented", 403)
    if matter_id:
        matter = await db.scalar(
            select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
        )
        if not matter:
            raise SmsError("Matter was not found", 404)
        is_matter_party = await db.scalar(
            select(MatterParty.id).where(
                MatterParty.tenant_id == tenant_id,
                MatterParty.matter_id == matter_id,
                MatterParty.contact_id == contact.id,
            )
        )
        if matter.client_contact_id != contact.id and not is_matter_party:
            raise SmsError("SMS recipient is not associated with the matter", 403)
    initial_config = await _config(db, tenant_id)
    provider_auth_token(initial_config)
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
        dispatch_attempt_id=attempt_id,
        dispatch_started_at=now,
        to_number=to_number,
        body=body,
        category=category,
        created_by_user_id=user_id,
    )
    db.add(message)
    observed_provider_message_id = None
    observed_provider_status = None
    try:
        await db.flush()
        # Reserve the idempotency key before any provider request. A concurrent
        # caller waits on the tenant/key unique constraint and then replays or
        # receives a reconciliation-required response, never a second dispatch.
        message.status = "dispatching"
        await db.commit()
        await set_tenant_context(db, str(tenant_id))
    except IntegrityError:
        await db.rollback()
        await set_tenant_context(db, str(tenant_id))
        replay = await db.scalar(
            select(SmsMessage).where(
                SmsMessage.tenant_id == tenant_id,
                SmsMessage.idempotency_key == idempotency_key,
            )
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
            SmsMessage.id == message.id,
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
            reconciliation_required=True,
        )
    dispatch_message_id = message.id
    if suppression.is_suppressed:
        message.status = "blocked_number_suppression"
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
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
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        await db.commit()
        raise SmsError("SMS consent changed before provider dispatch", 409)
    if in_quiet_hours(consent=locked_consent, now=locked_now):
        message.status = "blocked_quiet_hours"
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        await db.commit()
        raise SmsError("SMS is blocked during the recipient's quiet hours", 409)
    try:
        # Re-read the active generation immediately before submission. The
        # recipient suppression fence, not this tenant-global config row, stays
        # locked across provider I/O so unrelated destinations remain parallel.
        config = await _config(db, tenant_id)
        auth_token = provider_auth_token(config)
        provider_config_generation = config.generation
        account_sid = str(config.account_sid).strip()
        messaging_service_sid = str(config.messaging_service_sid or "").strip() or None
        from_number = str(config.from_number or "").strip() or None
        message.provider_config_generation = provider_config_generation
        message.provider_account_sid = account_sid
    except SmsError as exc:
        message.status = "blocked_provider_config"
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        await db.commit()
        raise SmsError(
            str(exc),
            exc.status_code,
            delivery_certainty="not_attempted",
            sms_message_id=dispatch_message_id,
        ) from exc
    try:
        data = {"To": to_number, "Body": body}
        if messaging_service_sid:
            data["MessagingServiceSid"] = messaging_service_sid
        else:
            data["From"] = from_number or ""
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
        if response.status_code >= 500 or response.status_code in (
            _TRANSIENT_PROVIDER_HTTP_CODES
        ):
            raise RuntimeError("provider server response did not prove rejection")
        if (
            response.status_code in _DETERMINISTIC_PROVIDER_REJECTION_CODES
            or provider_status
            in {
                "failed",
                "undelivered",
            }
        ):
            message.status = "provider_failed"
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
        message.from_number = payload.get("from") or from_number
        message.raw_provider_event = {"status": message.provider_status}
        _record_communication(
            db,
            tenant_id=tenant_id,
            message=message,
            user_id=user_id,
            status="submitted",
        )
        if before_success_commit:
            await before_success_commit(message)
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
            provider_message_id=observed_provider_message_id,
            provider_status=observed_provider_status,
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
        select(SmsMessage).where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.provider_message_id == provider_message_id,
        )
    )
    if replay:
        return replay
    from_number = normalize_e164(params.get("From"))
    to_number = params.get("To")
    body = (params.get("Body") or "").strip()[:1600]
    token = body.upper().split()[0] if body else ""
    contacts = (
        await db.scalars(
            select(Contact).where(
                Contact.tenant_id == tenant_id, Contact.phone.isnot(None)
            )
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
        client_matter_ids = (
            await db.scalars(
                select(Matter.id).where(
                    Matter.tenant_id == tenant_id,
                    Matter.client_contact_id.in_(candidate_ids),
                )
            )
        ).all()
        party_matter_ids = (
            await db.scalars(
                select(MatterParty.matter_id).where(
                    MatterParty.tenant_id == tenant_id,
                    MatterParty.contact_id.in_(candidate_ids),
                )
            )
        ).all()
        matter_ids = set(client_matter_ids) | set(party_matter_ids)
        if matter_ids:
            matters = (
                await db.scalars(
                    select(Matter)
                    .where(Matter.tenant_id == tenant_id, Matter.id.in_(matter_ids))
                    .order_by(Matter.id)
                )
            ).all()
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
        from_number=from_number,
        to_number=to_number,
        body=body,
        category="customer_reply",
        provider_status="received",
        raw_provider_event={"provider_message_id": provider_message_id},
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
        compliance = await apply_compliance_keyword(
            db,
            tenant_id=tenant_id,
            from_number=from_number,
            keyword=token,
            provider_message_id=provider_message_id,
        )
        message.raw_provider_event = {
            **message.raw_provider_event,
            "compliance": compliance,
        }
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
    if item.status != "pending":
        raise SmsError("SMS review item was already resolved", 409)
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
    now = datetime.now(timezone.utc)
    if decision == "reject":
        item.status = "rejected"
        item.reviewed_by_user_id = reviewer_user_id
        item.reviewed_at = now
        message.status = "route_rejected"
        return item
    if decision != "resolve" or not contact_id or not matter_id:
        raise SmsError("Resolution requires one contact and matter", 422)
    contact = await db.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
    )
    matter = await db.scalar(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    if contact is None or matter is None:
        raise SmsError("Resolution target was not found", 404)
    try:
        current_phone = normalize_e164(contact.phone)
    except SmsError as exc:
        raise SmsError(
            "Resolution contact does not match the inbound phone", 409
        ) from exc
    if current_phone != message.from_number:
        raise SmsError("Resolution contact does not match the inbound phone", 409)
    party = await db.scalar(
        select(MatterParty.id).where(
            MatterParty.tenant_id == tenant_id,
            MatterParty.matter_id == matter_id,
            MatterParty.contact_id == contact_id,
        )
    )
    if matter.client_contact_id != contact_id and party is None:
        raise SmsError("Resolution contact is not associated with the matter", 409)
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
    """Resolve uncertain work using operator non-send attestation or provider truth."""
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
    now = datetime.now(timezone.utc)
    if resolution == "confirmed_not_sent":
        message.status = "reconciled_not_sent"
        message.provider_status = message.provider_status or "unknown"
        resolved_certainty = "confirmed_not_sent"
        resolved_status = "failed"
        resolved_error = (
            "Operator attested that the provider did not send; create a new reviewed "
            "SMS proposal"
        )
    elif resolution == "provider_lookup":
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
        config = await _config(db, tenant_id)
        account_sid = str(config.account_sid or "").strip()
        if message.provider_account_sid and message.provider_account_sid != account_sid:
            raise SmsError(
                "The current provider account cannot verify this dispatch",
                409,
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_account_mismatch",
            )
        auth_token = provider_auth_token(config)
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
        try:
            provider_to = normalize_e164(payload.get("to"))
        except SmsError as exc:
            raise SmsError(
                "Provider lookup returned a mismatched message",
                409,
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_identity_mismatch",
            ) from exc
        if (
            str(payload.get("sid") or "") != lookup_sid
            or str(payload.get("account_sid") or "") != account_sid
            or provider_to != message.to_number
        ):
            raise SmsError(
                "Provider lookup returned a mismatched message",
                409,
                sms_message_id=message.id,
                reconciliation_required=True,
                code="sms_provider_identity_mismatch",
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
        duplicate = await db.scalar(
            select(SmsMessage.id).where(
                SmsMessage.tenant_id == tenant_id,
                SmsMessage.provider_message_id == lookup_sid,
                SmsMessage.id != message.id,
            )
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
        message.provider_config_generation = config.generation
        message.provider_status = incoming
        message.raw_provider_event = {
            **(message.raw_provider_event or {}),
            "reconciled_by": "provider_lookup",
            "status": incoming,
        }
        if incoming in {"failed", "undelivered"}:
            message.status = "failed"
            resolved_certainty = "confirmed_not_sent"
            resolved_status = "failed"
            resolved_error = "Provider lookup confirmed that the SMS was not delivered"
        elif incoming in {"delivered", "read"}:
            message.status = "delivered"
            resolved_certainty = "confirmed_sent"
            resolved_status = "sent"
            resolved_error = None
        else:
            message.status = "submitted"
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
    elif communication:
        communication.status = "failed"
    else:
        _record_communication(
            db,
            tenant_id=tenant_id,
            message=message,
            user_id=message.created_by_user_id,
            status="failed",
        )
    automation_run = await db.scalar(
        select(TaskAutomationRun)
        .where(
            TaskAutomationRun.tenant_id == tenant_id,
            TaskAutomationRun.sms_message_id == message.id,
        )
        .with_for_update()
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
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_id))
            rows = list(
                (
                    await db.scalars(
                        select(SmsMessage)
                        .where(
                            SmsMessage.tenant_id == tenant_id,
                            SmsMessage.direction == "outbound",
                            or_(
                                (
                                    (SmsMessage.status == "dispatching")
                                    & (
                                        SmsMessage.dispatch_started_at
                                        <= dispatch_cutoff
                                    )
                                ),
                                (
                                    (SmsMessage.status == "submitted")
                                    & (SmsMessage.created_at <= submitted_cutoff)
                                    & SmsMessage.reconciliation_required_at.is_(None)
                                ),
                            ),
                        )
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for row in rows:
                reason = (
                    "dispatch_lease_expired"
                    if row.status == "dispatching"
                    else "signed_status_callback_overdue"
                )
                if row.status == "dispatching":
                    row.status = "provider_unknown"
                row.reconciliation_required_at = now
                row.raw_provider_event = {
                    **(row.raw_provider_event or {}),
                    "reconciliation_reason": reason,
                }
            if rows:
                await db.commit()
                changed += len(rows)
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
            else ("failed" if incoming in {"failed", "undelivered"} else "submitted")
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
            message.reconciliation_resolution = "signed_provider_callback"
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
        automation_run = await db.scalar(
            select(TaskAutomationRun)
            .where(
                TaskAutomationRun.tenant_id == tenant_id,
                TaskAutomationRun.action_type == "sms_client",
                or_(
                    TaskAutomationRun.sms_message_id == message.id,
                    TaskAutomationRun.provider_message_id == sid,
                    TaskAutomationRun.action_snapshot["idempotency_key"].as_string()
                    == message.idempotency_key,
                ),
            )
            .order_by(TaskAutomationRun.created_at.desc(), TaskAutomationRun.id.desc())
            .limit(1)
            .with_for_update()
        )
        if automation_run:
            automation_run.sms_message_id = message.id
            automation_run.reconciliation_required = False
            if incoming in {"delivered", "read"}:
                automation_run.status = "sent"
                automation_run.delivery_certainty = "confirmed_sent"
                automation_run.error_message = None
            elif incoming in {"failed", "undelivered"}:
                automation_run.status = "failed"
                automation_run.delivery_certainty = "provider_rejected"
                automation_run.error_message = (
                    "SMS delivery failed after provider acceptance"
                )
            else:
                automation_run.status = "submitted"
                automation_run.delivery_certainty = "provider_accepted"
        await db.commit()
    return message
