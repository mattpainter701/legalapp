"""Fail-closed Twilio adapter and tenant-scoped SMS reconciliation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    return await db.scalar(
        select(LeadChannelConsent)
        .join(Lead, Lead.id == LeadChannelConsent.lead_id)
        .where(
            LeadChannelConsent.tenant_id == tenant_id,
            Lead.tenant_id == tenant_id,
            Lead.contact_id == contact_id,
        )
    )


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
    replay = await db.scalar(
        select(SmsMessage).where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.idempotency_key == idempotency_key,
        )
    )
    if replay:
        return replay
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
    ):
        raise SmsError("SMS follow-up is not currently consented", 403)
    if matter_id:
        matter = await db.scalar(
            select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
        )
        if not matter:
            raise SmsError("Matter was not found", 404)
    now = datetime.now(timezone.utc)
    message = SmsMessage(
        tenant_id=tenant_id,
        contact_id=contact.id,
        matter_id=matter_id,
        idempotency_key=idempotency_key,
        direction="outbound",
        status="queued",
        to_number=to_number,
        body=body,
        category=category,
        created_by_user_id=user_id,
    )
    db.add(message)
    await db.flush()
    if in_quiet_hours(consent=consent, now=now):
        message.status = "blocked_quiet_hours"
        db.add(
            _communication(
                tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
            )
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
            db.add(
                _communication(
                    tenant_id=tenant_id,
                    message=message,
                    user_id=user_id,
                    status="failed",
                )
            )
            await db.commit()
            raise SmsError("SMS provider did not accept the message", 503)
        message.provider_message_id = str(payload["sid"])
        message.provider_status = str(payload.get("status") or "queued")
        message.status = "submitted"
        message.from_number = payload.get("from") or config.from_number
        message.raw_provider_event = {"status": message.provider_status}
        db.add(
            _communication(
                tenant_id=tenant_id, message=message, user_id=user_id, status="sent"
            )
        )
        await db.commit()
        return message
    except SmsError:
        raise
    except Exception as exc:
        message.status = "provider_unknown"
        message.raw_provider_event = {"failure": type(exc).__name__}
        db.add(
            _communication(
                tenant_id=tenant_id, message=message, user_id=user_id, status="failed"
            )
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
    db.add(_communication(tenant_id=tenant_id, message=message, status="received"))
    await db.commit()
    return message


async def apply_status(
    db: AsyncSession, *, tenant_id, params: dict[str, str]
) -> SmsMessage:
    sid = params.get("MessageSid") or ""
    message = await db.scalar(
        select(SmsMessage).where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.provider_message_id == sid,
        )
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
        await db.commit()
    return message
