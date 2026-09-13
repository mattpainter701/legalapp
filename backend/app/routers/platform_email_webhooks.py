"""Inbound delivery-event webhook for the platform's transactional mail relay.

The relay reports hard bounces and spam complaints here so the suppression list
stays current without an operator reading a bounce mailbox. Postmark's payload
shape is the default; the handler reads only fields that SES and Mailgun also
carry equivalents for, so swapping providers is a mapping change rather than a
rewrite.

Authentication is a bearer secret, not an HMAC over the body: Postmark does not
sign webhook payloads, and its documented mechanism is a secret embedded in the
endpoint URL or basic-auth credentials. The secret is compared in constant time
and the endpoint answers 2xx for anything it chooses not to act on, so a caller
cannot probe which addresses are known.
"""

import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.email_suppression import PlatformEmailWebhookEvent
from app.services.email_suppression import record_suppression

router = APIRouter(prefix="/api/platform/email", tags=["platform-email"])
logger = logging.getLogger(__name__)
settings = get_settings()

# Postmark bounce types that mean the mailbox is permanently gone. Everything
# absent from this map (Transient, SoftBounce, DnsError, Blocked, SpamNotify
# on a shared IP, …) is logged and dropped: a temporary failure at the
# recipient's mail host must never cost a user their password reset.
_PERMANENT_BOUNCE_TYPES = {
    "HardBounce": "hard_bounce",
    "BadEmailAddress": "bad_mailbox",
    "ManuallyDeactivated": "manual",
    "Unsubscribe": "manual",
}

_MAX_WEBHOOK_BYTES = 256 * 1024


def _authorize(request: Request) -> None:
    secret = settings.PLATFORM_EMAIL_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(
            status_code=503, detail="Platform email webhook is not configured"
        )
    supplied = request.headers.get("authorization", "")
    scheme, _, token = supplied.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), secret):
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")


async def _claim_event(
    db: AsyncSession, *, provider: str, event_id: str, record_type: str | None
) -> bool:
    """Record the delivery, returning False when it has already been applied."""
    stmt = (
        pg_insert(PlatformEmailWebhookEvent)
        .values(provider=provider, event_id=event_id, record_type=record_type)
        .on_conflict_do_nothing(index_elements=["provider", "event_id"])
        .returning(PlatformEmailWebhookEvent.id)
    )
    claimed = (await db.execute(stmt)).scalar_one_or_none()
    await db.commit()
    return claimed is not None


@router.post("/webhook/postmark", status_code=status.HTTP_202_ACCEPTED)
async def postmark_delivery_event(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Apply a Postmark bounce or spam-complaint event to the suppression list."""
    _authorize(request)

    body = await request.body()
    if len(body) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    record_type = str(payload.get("RecordType") or "").strip()
    recipient = str(payload.get("Email") or payload.get("Recipient") or "").strip()
    # Postmark sends a numeric bounce ID for bounces and a MessageID for
    # complaints; either is stable for the lifetime of the event.
    event_id = str(payload.get("ID") or payload.get("MessageID") or "").strip()

    if not recipient or not event_id:
        # Acknowledged so the provider stops retrying a payload we can never
        # act on, but never silently: a shape change should be visible.
        logger.warning(
            "Postmark webhook missing recipient or event id (record_type=%s)",
            record_type or "unknown",
        )
        return {"status": "ignored"}

    if record_type == "SpamComplaint":
        reason = "spam_complaint"
    elif record_type == "Bounce":
        reason = _PERMANENT_BOUNCE_TYPES.get(str(payload.get("Type") or "").strip())
        if reason is None:
            logger.info(
                "Transient Postmark bounce not suppressed (type=%s)",
                payload.get("Type"),
            )
            return {"status": "ignored", "record_type": record_type}
    else:
        return {"status": "ignored", "record_type": record_type or "unknown"}

    if not await _claim_event(
        db, provider="postmark", event_id=event_id, record_type=record_type
    ):
        logger.info("Duplicate Postmark webhook delivery ignored (id=%s)", event_id)
        return {"status": "duplicate"}

    added = await record_suppression(
        recipient,
        reason=reason,
        provider="postmark",
        detail=str(payload.get("Description") or payload.get("Details") or "")[:500]
        or None,
        provider_payload={
            "record_type": record_type,
            "type": payload.get("Type"),
            "message_id": payload.get("MessageID"),
            "bounced_at": payload.get("BouncedAt"),
        },
        db=db,
    )
    logger.info(
        "Postmark %s applied (reason=%s, newly_suppressed=%s)",
        record_type,
        reason,
        added,
    )
    return {"status": "applied", "reason": reason, "newly_suppressed": added}
