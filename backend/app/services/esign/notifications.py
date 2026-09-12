"""Email delivery and reminder orchestration for native e-sign requests."""

import logging

from datetime import datetime, timezone
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.signature import SignatureRequest, SignatureSigner
from app.services.email import EmailDeliveryResult, email_service
from app.services.esign.service import next_pending_signers

logger = logging.getLogger(__name__)


def _audit(signer: SignatureSigner) -> dict:
    return dict(signer.audit or {})


async def notify_signer(signer, request, *, kind="invitation"):
    document_name = request.source_document_filename or "a document"
    url = f"{get_settings().FRONTEND_URL.rstrip('/')}/client-portal"
    action = {
        "reminder": "Reminder: signature requested",
        # The firm returned an uploaded signed copy; the client signs again.
        "resubmit": "Please sign again",
    }.get(kind, "Signature requested")
    instruction = (
        "Your legal team could not accept the signed copy you uploaded. Please sign"
        if kind == "resubmit"
        else "Please review and sign"
    )
    result = await email_service.send_email(
        [signer.email],
        f"{action}: {document_name}",
        f'<p>Hello {escape(signer.name)},</p><p>{instruction} <strong>{escape(document_name)}</strong> in the secure client portal.</p><p><a href="{escape(url)}">Open the client portal</a></p>',
        f"Hello {signer.name},\n\n{instruction} {document_name}:\n{url}\n",
    )
    audit = _audit(signer)
    stamp = datetime.now(timezone.utc).isoformat()
    audit[f"{kind}_delivery_status"] = result.value
    audit[f"{kind}_attempted_at"] = stamp
    if result is EmailDeliveryResult.SENT:
        audit[f"{kind}_sent_at"] = stamp
    signer.audit = audit
    return result


async def notify_actionable_signers(request, *, kind="invitation"):
    return [
        await notify_signer(signer, request, kind=kind)
        for signer in next_pending_signers(request)
    ]


def mark_signer_viewed(signer):
    audit = _audit(signer)
    audit.setdefault("viewed_at", datetime.now(timezone.utc).isoformat())
    signer.audit = audit


async def process_due_reminders(db: AsyncSession, *, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    rows = await db.execute(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(
            SignatureRequest.status.in_(["sent", "partially_signed"]),
            SignatureRequest.expires_at.isnot(None),
        )
    )
    sent = 0
    for request in rows.scalars().unique():
        if request.expires_at <= now:
            request.status = "expired"
            continue
        days_left = (request.expires_at.date() - now.date()).days
        if days_left not in set(
            (request.reminders or {}).get("days_before_expiration", [])
        ):
            continue
        key = f"reminder_{days_left}_days_sent_at"
        for signer in next_pending_signers(request):
            if _audit(signer).get(key):
                continue
            result = await notify_signer(signer, request, kind="reminder")
            if result is EmailDeliveryResult.SENT:
                audit = _audit(signer)
                audit[key] = now.isoformat()
                signer.audit = audit
                sent += 1
    await db.commit()
    return sent


async def notify_signer_sms(db, signer, request, *, kind="invitation"):
    """Text a signer about a document waiting for them, where consent covers it.

    Signature notifications have always been email-only, even for a client who
    asked to be texted. They are sent under the ``case_updates`` category, not
    intake: a client who agreed to onboarding texts has not agreed to be texted
    for the life of the matter, so a consent that covers only intake silently
    declines here rather than being stretched to fit.
    """
    from app.services.sms import SmsError, load_sms_consents, send_sms
    from app.services.sms_categories import (
        SMS_CATEGORY_CASE_UPDATES,
        allows_case_updates,
    )

    if not signer.contact_id:
        return None
    consents = await load_sms_consents(db, request.tenant_id, signer.contact_id)
    if not any(allows_case_updates(consent) for consent in consents):
        return None

    document_name = request.source_document_filename or "a document"
    url = f"{get_settings().FRONTEND_URL.rstrip('/')}/client-portal"
    lead = "Reminder" if kind == "reminder" else "Your legal team"
    body = (
        f"{lead}: {document_name} is ready for your signature in your secure "
        f"client portal: {url}"
    )
    audit = _audit(signer)
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        result = await send_sms(
            db,
            tenant_id=request.tenant_id,
            user_id=request.created_by_user_id,
            contact_id=signer.contact_id,
            matter_id=request.matter_id,
            body=body,
            category=SMS_CATEGORY_CASE_UPDATES,
            idempotency_key=f"signature:{request.id}:{signer.id}:{kind}",
        )
    except SmsError as exc:
        audit[f"{kind}_sms_status"] = exc.code or "failed"
        audit[f"{kind}_sms_attempted_at"] = stamp
        signer.audit = audit
        return None
    audit[f"{kind}_sms_status"] = result.delivery_certainty
    audit[f"{kind}_sms_attempted_at"] = stamp
    signer.audit = audit
    return result


async def notify_actionable_signers_sms(db, request, *, kind="invitation"):
    """SMS the signers whose turn it is, alongside the email they already get."""
    sent = []
    for signer in next_pending_signers(request):
        try:
            result = await notify_signer_sms(db, signer, request, kind=kind)
        except Exception:
            # A text is an extra channel on top of the email that already went.
            # Losing it must never fail the send it accompanies.
            logger.exception(
                "Signature SMS could not be attempted for request %s", request.id
            )
            continue
        if result is not None:
            sent.append(result)
    return sent
