"""Inbound delivery-event webhook for the platform's transactional mail relay.

Resend reports bounces and spam complaints here so the suppression list stays
current without an operator reading a bounce mailbox.

Deliveries are signed with Svix: an HMAC-SHA256 over ``id.timestamp.body``. The
signature therefore covers the payload itself rather than merely proving the
caller knows a shared secret, and the timestamp window keeps a captured
delivery from being replayed later. Resend is built on SES, so its bounce
classification carries SES's ``Permanent``/``Transient`` distinction through
unchanged — which is the field that decides whether an address is suppressed.
"""

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

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

# Svix's own default replay window, and not an operator-tunable knob.
_SIGNATURE_TOLERANCE_SECONDS = 300

_MAX_WEBHOOK_BYTES = 256 * 1024

# Resend passes SES's bounce sub-type through. A sub-type that names a dead
# mailbox is recorded as such; any other permanent failure is a plain hard
# bounce. Transient and Undetermined never reach this map — see below.
_PERMANENT_SUBTYPES = {
    "NoEmail": "bad_mailbox",
    "Suppressed": "bad_mailbox",
    "OnAccountSuppressionList": "bad_mailbox",
}


def verify_svix_signature(
    *,
    secret: str,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    body: bytes,
    now: datetime | None = None,
) -> bool:
    """Verify a Svix-signed delivery and reject stale timestamp windows."""
    if not (secret and svix_id and svix_timestamp and svix_signature):
        return False

    try:
        signed_at = datetime.fromtimestamp(int(svix_timestamp), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return False
    current = now or datetime.now(timezone.utc)
    if abs((current - signed_at).total_seconds()) > _SIGNATURE_TOLERANCE_SECONDS:
        return False

    key = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
    try:
        key_bytes = base64.b64decode(key, validate=True)
    except ValueError:
        return False

    signed_content = b".".join(
        (svix_id.encode("utf-8"), svix_timestamp.encode("utf-8"), body)
    )
    expected = base64.b64encode(
        hmac.new(key_bytes, signed_content, hashlib.sha256).digest()
    ).decode("ascii")

    # The header carries a space-delimited list so a secret can be rotated with
    # both signatures live. Any one version-1 match authenticates the delivery.
    for candidate in svix_signature.split():
        version, _, value = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(expected, value):
            return True
    return False


def _authorize(request: Request, body: bytes) -> None:
    secret = settings.PLATFORM_EMAIL_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(
            status_code=503, detail="Platform email webhook is not configured"
        )
    if not verify_svix_signature(
        secret=secret,
        svix_id=request.headers.get("svix-id", "").strip(),
        svix_timestamp=request.headers.get("svix-timestamp", "").strip(),
        svix_signature=request.headers.get("svix-signature", "").strip(),
        body=body,
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


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


def _sole_recipient(data: dict) -> str | None:
    """Return the recipient a delivery event can be attributed to, if exactly one.

    ``data.to`` is a list and the event does not say which entry failed. With
    several recipients the bounce cannot be attributed, and suppressing all of
    them would lock working mailboxes out of password reset over someone else's
    dead address — so such an event is acknowledged and dropped instead.
    """
    recipients = data.get("to")
    if isinstance(recipients, str):
        recipients = [recipients]
    if not isinstance(recipients, list) or len(recipients) != 1:
        return None
    recipient = recipients[0]
    return recipient.strip() if isinstance(recipient, str) else None


@router.post("/webhook/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_delivery_event(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Apply a Resend bounce or spam-complaint event to the suppression list."""
    body = await request.body()
    if len(body) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    _authorize(request, body)

    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    event_type = str(payload.get("type") or "").strip()
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}

    # Svix's message id identifies this delivery event. Resend's own
    # ``email_id`` identifies the message, and a bounce and a later complaint
    # for the same message share it, so it would collapse two distinct events.
    event_id = request.headers.get("svix-id", "").strip()

    if event_type == "email.complained":
        reason = "spam_complaint"
    elif event_type == "email.bounced":
        bounce = data.get("bounce")
        if not isinstance(bounce, dict):
            bounce = {}
        bounce_type = str(bounce.get("type") or "").strip()
        # Only a permanent failure suppresses. Transient (a full mailbox,
        # greylisting) and Undetermined must not cost a user their password
        # reset over a momentary outage at their mail host.
        if bounce_type != "Permanent":
            logger.info(
                "Non-permanent Resend bounce not suppressed (type=%s, subtype=%s)",
                bounce_type or "unknown",
                bounce.get("subType"),
            )
            return {"status": "ignored", "type": event_type}
        reason = _PERMANENT_SUBTYPES.get(
            str(bounce.get("subType") or "").strip(), "hard_bounce"
        )
    else:
        return {"status": "ignored", "type": event_type or "unknown"}

    recipient = _sole_recipient(data)
    if not recipient or not event_id:
        # Acknowledged so the provider stops retrying something that can never
        # be applied, but never silently: a payload shape change should show up.
        logger.warning(
            "Resend webhook not attributable to one recipient (type=%s, "
            "recipients=%s, signed=%s)",
            event_type,
            len(data.get("to") or []),
            bool(event_id),
        )
        return {"status": "ignored"}

    if not await _claim_event(
        db, provider="resend", event_id=event_id, record_type=event_type
    ):
        logger.info("Duplicate Resend webhook delivery ignored (id=%s)", event_id)
        return {"status": "duplicate"}

    bounce = data.get("bounce") if isinstance(data.get("bounce"), dict) else {}
    added = await record_suppression(
        recipient,
        reason=reason,
        provider="resend",
        detail=str(bounce.get("message") or "")[:500] or None,
        provider_payload={
            "type": event_type,
            "bounce_type": bounce.get("type"),
            "bounce_subtype": bounce.get("subType"),
            "email_id": data.get("email_id"),
            "created_at": payload.get("created_at"),
        },
        db=db,
    )
    logger.info(
        "Resend %s applied (reason=%s, newly_suppressed=%s)",
        event_type,
        reason,
        added,
    )
    return {"status": "applied", "reason": reason, "newly_suppressed": added}
