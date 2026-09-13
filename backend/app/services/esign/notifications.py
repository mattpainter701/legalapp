"""Email delivery and reminder orchestration for native e-sign requests."""

import logging

from datetime import datetime, timezone
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.plugin import Matter
from app.models.signature import SignatureRequest, SignatureSigner
from app.models.user import User
from app.services.connected_mail import send_client_email
from app.services.email import EmailDeliveryResult, email_service
from app.services.esign.service import next_pending_signers

logger = logging.getLogger(__name__)


def _audit(signer: SignatureSigner) -> dict:
    return dict(signer.audit or {})


async def notify_signer(db, signer, request, *, kind="invitation"):
    """Email a signer from the requesting user's connected mailbox.

    Intake paperwork already leaves through the mailbox of the person who sent
    it (Google or Microsoft, then a firm mailbox, then SMTP), so the client
    sees the same sender for a signature request as for the packet that
    introduced it, and the firm's sent folder records who sent what. Platform
    SMTP is only the last resort; where it is switched off the invitation is
    reported undelivered rather than silently dropped.
    """
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
    delivery = await send_client_email(
        db,
        tenant_id=request.tenant_id,
        actor_user_id=request.created_by_user_id,
        to=[signer.email],
        subject=f"{action}: {document_name}",
        html_body=f'<p>Hello {escape(signer.name)},</p><p>{instruction} <strong>{escape(document_name)}</strong> in the secure client portal.</p><p><a href="{escape(url)}">Open the client portal</a></p>',
        text_body=f"Hello {signer.name},\n\n{instruction} {document_name}:\n{url}\n",
        smtp_service=email_service,
    )
    result = delivery.result
    audit = _audit(signer)
    stamp = datetime.now(timezone.utc).isoformat()
    audit[f"{kind}_delivery_status"] = result.value
    audit[f"{kind}_attempted_at"] = stamp
    if delivery.provider:
        audit[f"{kind}_provider"] = delivery.provider
    if result is EmailDeliveryResult.SENT:
        audit[f"{kind}_sent_at"] = stamp
    elif delivery.detail:
        audit[f"{kind}_delivery_detail"] = delivery.detail[:500]
    signer.audit = audit
    return result


async def notify_actionable_signers(db, request, *, kind="invitation"):
    return [
        await notify_signer(db, signer, request, kind=kind)
        for signer in next_pending_signers(request)
    ]


async def notify_requester_signed(db, request):
    """Tell the person who sent the request that every signer has signed.

    Sent the moment the last signature lands, before the executed copy is
    filed: filing can wait on the firm's storage, and the sender needs to know
    the client acted whether or not they are watching the matter. Delivery
    goes through the sender's own connected mailbox like every other message
    here. Returns the delivery result, or None when there is nobody to tell.
    """
    if not request.created_by_user_id:
        return None
    user = await db.scalar(
        select(User).where(
            User.id == request.created_by_user_id,
            User.tenant_id == request.tenant_id,
        )
    )
    if user is None or not (user.email or "").strip():
        return None
    matter = await db.scalar(
        select(Matter).where(
            Matter.id == request.matter_id, Matter.tenant_id == request.tenant_id
        )
    )
    matter_name = (matter.matter_name if matter else None) or "the matter"
    document_name = request.source_document_filename or "a document"
    signed = [
        signer
        for signer in sorted(request.signers or [], key=lambda row: row.sign_order)
        if signer.status == "signed"
    ]
    names = ", ".join(signer.name for signer in signed if signer.name) or "The signer"
    filed = request.status == "completed"
    filing_note = (
        "The signed copy and its evidence certificate are filed to the matter."
        if filed
        else "The signed copy is being filed to the matter and will appear there "
        "shortly; the Signatures panel reports if filing is held up."
    )
    url = f"{get_settings().FRONTEND_URL.rstrip('/')}/matters/{request.matter_id}"
    subject = f"Signed: {document_name} — {matter_name}"
    html = (
        f"<p>{escape(names)} signed <strong>{escape(document_name)}</strong> "
        f"for {escape(matter_name)}.</p><p>{escape(filing_note)}</p>"
        f'<p><a href="{escape(url)}">Open the matter</a></p>'
    )
    text = (
        f"{names} signed {document_name} for {matter_name}.\n\n{filing_note}\n{url}\n"
    )
    delivery = await send_client_email(
        db,
        tenant_id=request.tenant_id,
        actor_user_id=user.id,
        to=[user.email],
        subject=subject,
        html_body=html,
        text_body=text,
        smtp_service=email_service,
    )
    if delivery.result is not EmailDeliveryResult.SENT:
        logger.warning(
            "Signed notice for request %s not delivered to %s: %s",
            request.id,
            user.email,
            delivery.detail or delivery.result.value,
        )
    return delivery.result


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
            result = await notify_signer(db, signer, request, kind="reminder")
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
