"""Chase a signature the way intake chases a requirement.

A signature request sent mid-case used to have no deadline and nothing
watching it: the firm sent it, the provider reminded the signer, and whether
anyone followed up was a memory problem. A dated request now raises one
assigned follow-up task, keyed to the request so repeated sends or reminder
passes cannot duplicate it, and closes that task the moment the request
reaches a terminal state.
"""

from sqlalchemy import select

from app.models.matter_intake import MatterIntake
from app.services.matter_followups import close_followup_task, ensure_followup_task

FOLLOWUP_KIND = "signature_due"

# Terminal states: nothing about the request can change after these, so the
# firm should not still be holding a task to chase it.
CLOSING_STATUSES = {"completed", "declined", "voided", "expired"}


async def matter_timezone(db, tenant_id, matter_id):
    """The timezone the client was last given a deadline in.

    Intake records the client's own timezone. Re-using it keeps "due Friday"
    meaning the same thing in month four as it did at intake, and falls back
    to UTC for a matter that never ran intake.
    """
    config = await db.scalar(
        select(MatterIntake.config).where(
            MatterIntake.tenant_id == tenant_id, MatterIntake.matter_id == matter_id
        )
    )
    return (config or {}).get("timezone") or "UTC"


def _signer_contact_id(req):
    for signer in req.signers or []:
        if signer.contact_id:
            return signer.contact_id
    return None


def _label(req):
    return req.source_document_filename or "Document"


async def ensure_signature_followup(db, req):
    """Raise the follow-up for a dated request. A no-op without a due date."""
    if not req.due_at or req.status in CLOSING_STATUSES:
        return None
    return await ensure_followup_task(
        db,
        tenant_id=req.tenant_id,
        matter_id=req.matter_id,
        namespace=req.id,
        kind=FOLLOWUP_KIND,
        title=f"Signature due from client: {_label(req)}",
        due=req.due_at,
        timezone_name=await matter_timezone(db, req.tenant_id, req.matter_id),
        owner_id=req.created_by_user_id,
        created_by=req.created_by_user_id,
        contact_id=_signer_contact_id(req),
        description=(
            f"{_label(req)} was sent for signature and is due {req.due_at.isoformat()}. "
            "Follow up with the client if it has not been signed."
        ),
        source="signature",
        external_ref=f"signature:{req.id}",
    )


async def close_signature_followup(db, req, reason):
    """Cancel the follow-up once the request can no longer be acted on."""
    if not req.due_at:
        return None
    return await close_followup_task(
        db,
        tenant_id=req.tenant_id,
        namespace=req.id,
        kind=FOLLOWUP_KIND,
        reason=reason,
        actor_user_id=req.created_by_user_id,
    )
