"""Email delivery and reminder orchestration for native e-sign requests."""

from datetime import datetime, timezone
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.signature import SignatureRequest, SignatureSigner
from app.services.email import EmailDeliveryResult, email_service
from app.services.esign.service import next_pending_signers


def _audit(signer: SignatureSigner) -> dict:
    return dict(signer.audit or {})


async def notify_signer(signer, request, *, kind="invitation"):
    document_name = request.source_document_filename or "a document"
    url = f"{get_settings().FRONTEND_URL.rstrip('/')}/client-portal"
    action = "Reminder: signature requested" if kind == "reminder" else "Signature requested"
    result = await email_service.send_email(
        [signer.email], f"{action}: {document_name}",
        f"<p>Hello {escape(signer.name)},</p><p>Please review and sign <strong>{escape(document_name)}</strong> in the secure client portal.</p><p><a href=\"{escape(url)}\">Open the client portal</a></p>",
        f"Hello {signer.name},\n\nPlease review and sign {document_name}:\n{url}\n",
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
    return [await notify_signer(signer, request, kind=kind) for signer in next_pending_signers(request)]


def mark_signer_viewed(signer):
    audit = _audit(signer)
    audit.setdefault("viewed_at", datetime.now(timezone.utc).isoformat())
    signer.audit = audit


async def process_due_reminders(db: AsyncSession, *, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    rows = await db.execute(select(SignatureRequest).options(selectinload(SignatureRequest.signers)).where(
        SignatureRequest.status.in_(["sent", "partially_signed"]), SignatureRequest.expires_at.isnot(None)))
    sent = 0
    for request in rows.scalars().unique():
        if request.expires_at <= now:
            request.status = "expired"
            continue
        days_left = (request.expires_at.date() - now.date()).days
        if days_left not in set((request.reminders or {}).get("days_before_expiration", [])):
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
