"""Fail-closed Twilio adapter and tenant-scoped SMS reconciliation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_context
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.conversion_loop import LeadChannelConsent
from app.models.plugin import Matter
from app.models.sms import SmsMessage, SmsProviderConfig, SmsReviewItem
from app.services.token_vault import decrypt_token


class SmsError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


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
    if not value:
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
        return False
    try:
        local = now.astimezone(ZoneInfo(consent.consent_timezone)).time()
    except ZoneInfoNotFoundError:
        return True
    if start == end:
        return True
    if start < end:
        return start <= local < end
    return local >= start or local < end


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


async def _config(db: AsyncSession, tenant_id) -> SmsProviderConfig:
    config = await db.scalar(
        select(SmsProviderConfig).where(
            SmsProviderConfig.tenant_id == tenant_id,
            SmsProviderConfig.provider == "twilio",
            SmsProviderConfig.is_active.is_(True),
        )
    )
    if (
        not config
        or not config.sender_ready
        or not config.account_sid
        or not config.encrypted_auth_token
        or not config.encrypted_webhook_secret
        or not (config.messaging_service_sid or config.from_number)
    ):
        raise SmsError("SMS provider is not configured for delivery", 503)
    return config


async def _consent_for_contact(db: AsyncSession, tenant_id, contact_id):
    rows = (
        await db.scalars(
            select(LeadChannelConsent)
            .join(Lead, Lead.id == LeadChannelConsent.lead_id)
            .where(
                LeadChannelConsent.tenant_id == tenant_id,
                Lead.tenant_id == tenant_id,
                Lead.contact_id == contact_id,
            )
            .order_by(LeadChannelConsent.id)
        )
    ).all()
    # Multiple linked leads are not an authorization signal. Conflicting,
    # revoked, or stale provenance must fail closed rather than be selected by
    # database order.
    return rows[0] if len(rows) == 1 else None


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
) -> SmsMessage:
    contact = await db.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
    )
    if not contact or not contact.is_active:
        raise SmsError("SMS recipient was not found", 404)
    to_number = normalize_e164(contact.phone)
    consent = await _consent_for_contact(db, tenant_id, contact.id)
    if (
        not consent
        or not consent.sms_allowed
        or consent.sms_status != "active"
        or not consent.phone_verified
        or consent.mobile_e164 != to_number
        or consent.revoked_at
        or not consent.consented_at
        or not consent.consent_source
        or not consent.disclosure_version
        or (
            consent.consent_expires_at
            and consent.consent_expires_at <= datetime.now(timezone.utc)
        )
    ):
        raise SmsError("SMS follow-up is not currently consented", 403)
    if matter_id:
        matter = await db.scalar(
            select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
        )
        if not matter:
            raise SmsError("Matter was not found", 404)
    if consent.allowed_categories and category not in consent.allowed_categories:
        raise SmsError("SMS category is not allowed by the consent", 403)
    request_digest = _request_digest(
        contact_id=contact.id,
        matter_id=matter_id,
        to_number=to_number,
        body=body,
        category=category,
    )
    replay = await db.scalar(
        select(SmsMessage).where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.idempotency_key == idempotency_key,
        )
    )
    if replay:
        if replay.request_digest != request_digest:
            raise SmsError("Idempotency key was reused for a different SMS", 409)
        if replay.status in {"dispatching", "provider_unknown"}:
            raise SmsError(
                "The original SMS outcome must be reconciled before retrying", 409
            )
        return replay
    now = datetime.now(timezone.utc)
    message = SmsMessage(
        tenant_id=tenant_id,
        contact_id=contact.id,
        matter_id=matter_id,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        direction="outbound",
        status="queued",
        to_number=to_number,
        body=body,
        category=category,
        created_by_user_id=user_id,
    )
    db.add(message)
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
            raise SmsError(
                "The original SMS is already being dispatched or requires reconciliation",
                409,
            )
        raise SmsError("SMS idempotency reservation failed", 409)
    if in_quiet_hours(consent=consent, now=now):
        message.status = "blocked_quiet_hours"
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        await db.commit()
        raise SmsError("SMS is blocked during the recipient's quiet hours", 409)
    try:
        config = await _config(db, tenant_id)
        auth_token = decrypt_token(config.encrypted_auth_token)
        data = {"To": to_number, "Body": body}
        if config.messaging_service_sid:
            data["MessagingServiceSid"] = config.messaging_service_sid
        else:
            data["From"] = config.from_number or ""
        async with httpx.AsyncClient(timeout=15) as http:
            response = await http.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{config.account_sid}/Messages.json",
                data=data,
                auth=(config.account_sid, auth_token),
            )
        payload = response.json() if response.content else {}
        if response.status_code >= 400 or not payload.get("sid"):
            message.status = "provider_failed"
            message.provider_status = payload.get("status")
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
            raise SmsError("SMS provider did not accept the message", 503)
        message.provider_message_id = str(payload["sid"])
        message.provider_status = str(payload.get("status") or "queued")
        message.status = "submitted"
        message.from_number = payload.get("from") or config.from_number
        message.raw_provider_event = {"status": message.provider_status}
        _record_communication(
            db,
            tenant_id=tenant_id,
            message=message,
            user_id=user_id,
            status="submitted",
        )
        await db.commit()
        return message
    except SmsError:
        raise
    except Exception as exc:
        message.status = "provider_unknown"
        message.raw_provider_event = {"failure": type(exc).__name__}
        _record_communication(
            db, tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
        )
        await db.commit()
        raise SmsError(
            "SMS provider outcome is uncertain; retry with the same key", 503
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
    if contact:
        matters = (
            await db.scalars(
                select(Matter).where(
                    Matter.tenant_id == tenant_id,
                    Matter.client_contact_id == contact.id,
                )
            )
        ).all()
    matter = matters[0] if len(matters) == 1 else None
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
    await db.flush()
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
    else:
        consent = await _consent_for_contact(db, tenant_id, contact.id)
        token = body.upper().split()[0] if body else ""
        if consent and token in {"STOP", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}:
            consent.sms_allowed = False
            consent.sms_status = "opted_out"
            consent.revoked_at = datetime.now(timezone.utc)
        elif consent and token in {"START", "UNSTOP"}:
            consent.sms_status = (
                "active"
                if consent.phone_verified and consent.mobile_e164 == from_number
                else "blocked"
            )
            consent.sms_allowed = consent.sms_status == "active"
            consent.revoked_at = None if consent.sms_allowed else consent.revoked_at
        elif consent and token == "HELP":
            consent.sms_status = "active" if consent.sms_allowed else consent.sms_status
    _record_communication(db, tenant_id=tenant_id, message=message, status="received")
    await db.commit()
    return message


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
    current = _provider_status_rank(message.provider_status)
    if _provider_status_rank(incoming) >= current:
        message.provider_status = incoming or message.provider_status
        message.status = (
            "delivered"
            if incoming == "delivered"
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
        communication = await db.scalar(
            select(CommunicationLog).where(
                CommunicationLog.tenant_id == tenant_id,
                CommunicationLog.external_ref == f"sms:{sid}",
            )
        )
        if communication:
            communication.status = (
                "delivered"
                if incoming == "delivered"
                else "failed"
                if incoming in {"failed", "undelivered"}
                else "submitted"
            )
        await db.commit()
    return message
