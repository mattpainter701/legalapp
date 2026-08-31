"""The bounded, review-first public lead acquisition and conversion loop."""

from __future__ import annotations

import hashlib
from html import escape
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user, require_admin
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.conversion_loop import (
    IntakeForm,
    IntakeSubmission,
    LeadAppointment,
    LeadChannelConsent,
    LeadFunnelEvent,
)
from app.models.scheduled_event import ScheduledEvent
from app.schemas.conversion_loop import (
    BookingCreate,
    ConsentUpdate,
    FollowUpCreate,
    IntakeFormCreate,
    IntakeFormResponse,
    IntakeSubmissionCreate,
    TriageDecision,
)
from app.services.email import EmailDeliveryResult, email_service
from app.services.sms import SmsError, normalize_e164, send_sms

router = APIRouter(tags=["conversion-loop"])
staff = APIRouter(prefix="/api/intake", tags=["conversion-loop"])
public = APIRouter(prefix="/api/public/intake", tags=["public-intake"])


def _slug(value: str) -> str:
    return value.strip().lower()


def _validate_answers(schema: dict, answers: dict) -> None:
    fields = schema.get("fields", [])
    if not isinstance(fields, list) or len(fields) > 100:
        raise HTTPException(422, "Form schema is invalid")
    for field in fields:
        if not isinstance(field, dict) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,80}", str(field.get("name", ""))
        ):
            raise HTTPException(422, "Form schema contains an invalid field")
        name = field["name"]
        visible = field.get("show_if")
        if isinstance(visible, dict) and answers.get(
            visible.get("field")
        ) != visible.get("value"):
            continue
        if field.get("required") and (
            answers.get(name) is None or str(answers.get(name)).strip() == ""
        ):
            raise HTTPException(422, f"{name} is required")
        if name in answers and len(str(answers[name])) > 5000:
            raise HTTPException(422, f"{name} is too long")


def _attribution(value: dict[str, str]) -> dict[str, str]:
    allowed = {
        "source",
        "medium",
        "campaign",
        "term",
        "content",
        "referrer",
        "landing_path",
    }
    return {
        k: str(v)[:500] for k, v in value.items() if k in allowed and str(v).strip()
    }


@staff.post("/forms", response_model=IntakeFormResponse, status_code=201)
async def create_form(
    body: IntakeFormCreate,
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    row = IntakeForm(
        tenant_id=current_user.tenant_id,
        created_by_user_id=current_user.id,
        schema_json=body.form_schema,
        slug=body.slug,
        name=body.name,
        is_active=body.is_active,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "A form with that slug already exists") from None
    await db.refresh(row)
    return row


@staff.get("/forms", response_model=list[IntakeFormResponse])
async def list_forms(
    current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    await set_tenant_context(db, str(current_user.tenant_id))
    return list(
        (
            await db.scalars(
                select(IntakeForm)
                .where(IntakeForm.tenant_id == current_user.tenant_id)
                .order_by(IntakeForm.created_at.desc())
            )
        ).all()
    )


async def _public_form(slug: str, db: AsyncSession) -> IntakeForm:
    row = await db.scalar(
        select(IntakeForm).where(
            IntakeForm.slug == _slug(slug), IntakeForm.is_active.is_(True)
        )
    )
    if not row:
        raise HTTPException(404, "Intake form not found")
    await set_tenant_context(db, str(row.tenant_id))
    return row


@public.get("/{slug}")
async def get_public_form(slug: str, db: AsyncSession = Depends(get_db)):
    row = await _public_form(slug, db)
    return {
        "slug": row.slug,
        "name": row.name,
        "version": row.version,
        "schema": row.schema_json,
    }


@public.post("/{slug}/submissions", status_code=201)
async def submit_public_form(
    slug: str,
    body: IntakeSubmissionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    row = await _public_form(slug, db)
    if body.website:
        raise HTTPException(422, "Submission rejected")
    _validate_answers(row.schema_json, body.answers)
    if (
        not str(body.answers.get("email", "")).strip()
        and not str(body.answers.get("phone", "")).strip()
    ):
        raise HTTPException(422, "An email address or phone number is required")
    existing = await db.scalar(
        select(IntakeSubmission).where(
            IntakeSubmission.tenant_id == row.tenant_id,
            IntakeSubmission.idempotency_key == body.idempotency_key,
        )
    )
    if existing:
        return {
            "submission_id": str(existing.id),
            "lead_id": str(existing.lead_id),
            "replayed": True,
        }
    email = str(body.answers.get("email", "")).strip().lower() or None
    phone = str(body.answers.get("phone", "")).strip() or None
    contact = Contact(
        tenant_id=row.tenant_id,
        contact_type="prospect",
        first_name=body.answers.get("first_name"),
        last_name=body.answers.get("last_name"),
        organization_name=body.answers.get("organization_name"),
        email=email,
        phone=phone,
        email_opt_in=body.email_consent,
        sms_opt_in=body.sms_consent,
        referral_source=_attribution(body.attribution).get("source"),
    )
    db.add(contact)
    await db.flush()
    lead = Lead(
        tenant_id=row.tenant_id,
        contact_id=contact.id,
        source=_attribution(body.attribution).get("source", "website"),
        practice_area=body.answers.get("practice_area"),
        description=body.answers.get("description"),
        status="new",
    )
    db.add(lead)
    await db.flush()
    submission = IntakeSubmission(
        tenant_id=row.tenant_id,
        form_id=row.id,
        lead_id=lead.id,
        idempotency_key=body.idempotency_key,
        answers=body.answers,
        attribution=_attribution(body.attribution),
        source_ip_hash=hashlib.sha256(
            (request.client.host if request.client else "unknown").encode()
        ).hexdigest(),
    )
    db.add(submission)
    mobile_e164 = None
    if body.sms_consent:
        try:
            mobile_e164 = normalize_e164(phone)
        except SmsError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
    db.add(
        LeadChannelConsent(
            tenant_id=row.tenant_id,
            lead_id=lead.id,
            email_allowed=body.email_consent,
            sms_allowed=body.sms_consent,
            sms_status="pending_verification" if body.sms_consent else "unknown",
            disclosure_version=body.disclosure_version,
            mobile_e164=mobile_e164,
            consented_at=datetime.now(timezone.utc) if body.sms_consent else None,
            consent_source="public_intake" if body.sms_consent else None,
            consent_language=body.consent_language,
            consent_timezone=body.consent_timezone,
            quiet_hours_start=body.quiet_hours_start,
            quiet_hours_end=body.quiet_hours_end,
        )
    )
    db.add(
        LeadFunnelEvent(
            tenant_id=row.tenant_id,
            lead_id=lead.id,
            event_type="intake_submitted",
            source=submission.attribution.get("source"),
            metadata_json=submission.attribution,
        )
    )
    await db.commit()
    return {
        "submission_id": str(submission.id),
        "lead_id": str(lead.id),
        "replayed": False,
    }


@public.get("/{slug}/availability")
async def public_availability(slug: str, db: AsyncSession = Depends(get_db)):
    row = await _public_form(slug, db)
    slots = row.schema_json.get("availability", [])
    return {"slots": slots if isinstance(slots, list) else []}


@public.post("/{slug}/book", status_code=201)
async def book_public(
    slug: str, body: BookingCreate, db: AsyncSession = Depends(get_db)
):
    row = await _public_form(slug, db)
    lead = await db.scalar(
        select(Lead).where(Lead.id == body.lead_id, Lead.tenant_id == row.tenant_id)
    )
    if not lead:
        raise HTTPException(404, "Lead not found")
    available = row.schema_json.get("availability", [])
    if not isinstance(available, list):
        available = []
    slot = next(
        (
            s
            for s in available
            if isinstance(s, dict)
            and s.get("start_at") == body.start_at.isoformat()
            and s.get("end_at") == body.end_at.isoformat()
        ),
        None,
    )
    if slot is None:
        raise HTTPException(409, "That appointment slot is not available")
    existing_key = await db.scalar(
        select(LeadAppointment).where(
            LeadAppointment.tenant_id == row.tenant_id,
            LeadAppointment.idempotency_key == body.idempotency_key,
        )
    )
    if existing_key:
        return {
            "appointment_id": str(existing_key.id),
            "status": existing_key.status,
            "reminder_status": existing_key.reminder_status,
            "replayed": True,
        }
    if await db.scalar(
        select(LeadAppointment).where(
            LeadAppointment.tenant_id == row.tenant_id,
            LeadAppointment.start_at == body.start_at,
            LeadAppointment.status == "booked",
        )
    ):
        raise HTTPException(409, "That appointment slot was already booked")
    event = ScheduledEvent(
        tenant_id=row.tenant_id,
        title="Initial consultation",
        start_at=body.start_at,
        end_at=body.end_at,
        timezone=body.timezone,
        attendees=[],
        meeting_provider="none",
        sync_status="local",
    )
    db.add(event)
    await db.flush()
    appointment = LeadAppointment(
        tenant_id=row.tenant_id,
        lead_id=lead.id,
        scheduled_event_id=event.id,
        idempotency_key=body.idempotency_key,
        start_at=body.start_at,
        end_at=body.end_at,
        timezone=body.timezone,
    )
    db.add(appointment)
    db.add(
        LeadFunnelEvent(
            tenant_id=row.tenant_id,
            lead_id=lead.id,
            event_type="appointment_booked",
            source=lead.source,
        )
    )
    await db.commit()
    return {
        "appointment_id": str(appointment.id),
        "status": "booked",
        "reminder_status": "pending",
    }


@staff.post("/leads/{lead_id}/consent")
async def update_consent(
    lead_id: uuid.UUID,
    body: ConsentUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    lead = await db.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == current_user.tenant_id)
    )
    if not lead:
        raise HTTPException(404, "Lead not found")
    row = await db.scalar(
        select(LeadChannelConsent).where(
            LeadChannelConsent.tenant_id == current_user.tenant_id,
            LeadChannelConsent.lead_id == lead_id,
        )
    )
    if not row:
        row = LeadChannelConsent(tenant_id=current_user.tenant_id, lead_id=lead_id)
        db.add(row)
    mobile_e164 = None
    if body.sms_allowed:
        try:
            mobile_e164 = normalize_e164(body.mobile_e164)
        except SmsError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        if not body.phone_verified:
            raise HTTPException(422, "SMS consent requires a verified E.164 mobile")
    (
        row.email_allowed,
        row.sms_allowed,
        row.sms_status,
        row.phone_verified,
        row.disclosure_version,
        row.mobile_e164,
        row.consented_at,
        row.consent_source,
        row.consent_language,
        row.consent_timezone,
        row.quiet_hours_start,
        row.quiet_hours_end,
        row.allowed_categories,
        row.revoked_at,
    ) = (
        body.email_allowed,
        body.sms_allowed,
        "active" if body.sms_allowed and body.phone_verified else "unknown",
        body.phone_verified,
        body.disclosure_version,
        mobile_e164,
        datetime.now(timezone.utc) if body.sms_allowed else None,
        body.consent_source,
        body.consent_language,
        body.consent_timezone,
        body.quiet_hours_start,
        body.quiet_hours_end,
        body.allowed_categories,
        None if body.email_allowed or body.sms_allowed else datetime.now(timezone.utc),
    )
    contact = await db.scalar(
        select(Contact).where(
            Contact.id == lead.contact_id, Contact.tenant_id == current_user.tenant_id
        )
    )
    if contact:
        contact.sms_opt_in = row.sms_status == "active"
        contact.sms_opt_in_at = row.consented_at if contact.sms_opt_in else None
    await db.commit()
    return {
        "lead_id": str(lead_id),
        "email_allowed": row.email_allowed,
        "sms_allowed": row.sms_allowed,
        "revoked_at": row.revoked_at,
    }


@staff.post("/leads/{lead_id}/triage")
async def triage(
    lead_id: uuid.UUID,
    body: TriageDecision,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    lead = await db.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == current_user.tenant_id)
    )
    if not lead:
        raise HTTPException(404, "Lead not found")
    if body.decision == "clear":
        lead.conflict_check_status = "cleared"
        lead.status = "conflict_checked"
    elif body.decision == "decline":
        lead.status = "declined"
        lead.declined_reason = body.note
    else:
        lead.conflict_check_status = "conflict_found"
    db.add(
        LeadFunnelEvent(
            tenant_id=lead.tenant_id,
            lead_id=lead.id,
            event_type=f"triage_{body.decision}",
            source=lead.source,
            metadata_json={"note": body.note},
        )
    )
    await db.commit()
    return {
        "lead_id": str(lead.id),
        "status": lead.status,
        "conflict_check_status": lead.conflict_check_status,
    }


@staff.post("/leads/{lead_id}/follow-up")
async def send_follow_up(
    lead_id: uuid.UUID,
    body: FollowUpCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send only an explicitly authored, consent-checked follow-up.

    SMS remains unavailable until ECO-23–29's provider, signed webhooks and
    STOP/START state machine are configured; returning 503 is deliberate.
    """
    await set_tenant_context(db, str(current_user.tenant_id))
    lead = await db.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == current_user.tenant_id)
    )
    if not lead:
        raise HTTPException(404, "Lead not found")
    contact = await db.scalar(
        select(Contact).where(
            Contact.id == lead.contact_id, Contact.tenant_id == current_user.tenant_id
        )
    )
    consent = await db.scalar(
        select(LeadChannelConsent).where(
            LeadChannelConsent.tenant_id == current_user.tenant_id,
            LeadChannelConsent.lead_id == lead.id,
        )
    )
    allowed = bool(
        consent
        and not consent.revoked_at
        and (consent.email_allowed if body.channel == "email" else consent.sms_allowed)
    )
    if not allowed:
        raise HTTPException(403, f"{body.channel.upper()} follow-up is not consented")
    if body.channel == "sms":
        try:
            message = await send_sms(
                db,
                tenant_id=lead.tenant_id,
                user_id=current_user.id,
                contact_id=lead.contact_id,
                matter_id=lead.matter_id,
                body=body.body,
                category="lead_follow_up",
                idempotency_key=body.idempotency_key,
            )
        except SmsError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        return {
            "lead_id": str(lead.id),
            "channel": body.channel,
            "status": message.status,
            "provider_message_id": message.provider_message_id,
        }
    if not contact or not contact.email:
        raise HTTPException(422, "Lead has no email destination")
    external_ref = f"lead-follow-up:{body.idempotency_key}"
    replay = await db.scalar(
        select(CommunicationLog).where(
            CommunicationLog.tenant_id == lead.tenant_id,
            CommunicationLog.external_ref == external_ref,
        )
    )
    if replay:
        return {
            "lead_id": str(lead.id),
            "channel": body.channel,
            "status": replay.status,
            "replayed": True,
        }
    result = await email_service.send_email(
        [contact.email], body.subject, f"<p>{escape(body.body)}</p>", body.body
    )
    delivery_status = "sent" if result == EmailDeliveryResult.SENT else "failed"
    db.add(
        CommunicationLog(
            tenant_id=lead.tenant_id,
            contact_id=lead.contact_id,
            direction="outbound",
            channel="email",
            status=delivery_status,
            subject=body.subject,
            body=body.body,
            created_by_user_id=current_user.id,
            external_ref=external_ref,
        )
    )
    if result != EmailDeliveryResult.SENT:
        await db.commit()
        raise HTTPException(503, "Email provider did not accept the follow-up")
    db.add(
        LeadFunnelEvent(
            tenant_id=lead.tenant_id,
            lead_id=lead.id,
            event_type="follow_up_sent",
            source=lead.source,
            metadata_json={"channel": body.channel},
        )
    )
    await db.commit()
    return {"lead_id": str(lead.id), "channel": body.channel, "status": "sent"}


@staff.get("/funnel")
async def funnel(
    current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    await set_tenant_context(db, str(current_user.tenant_id))
    rows = (
        await db.execute(
            select(LeadFunnelEvent.event_type, func.count(LeadFunnelEvent.id))
            .where(LeadFunnelEvent.tenant_id == current_user.tenant_id)
            .group_by(LeadFunnelEvent.event_type)
        )
    ).all()
    return {"events": {event: count for event, count in rows}}


@staff.get("/abandoned")
async def abandoned_leads(
    current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Expose bounded recovery candidates; it never contacts a prospect itself."""
    await set_tenant_context(db, str(current_user.tenant_id))
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    leads = (
        await db.scalars(
            select(Lead)
            .where(
                Lead.tenant_id == current_user.tenant_id,
                Lead.source == "website",
                Lead.status.in_(("new", "contacted", "qualified")),
                Lead.created_at < cutoff,
            )
            .order_by(Lead.created_at.asc())
            .limit(100)
        )
    ).all()
    return {
        "candidates": [
            {
                "lead_id": str(lead.id),
                "status": lead.status,
                "created_at": lead.created_at,
            }
            for lead in leads
        ]
    }


@staff.post("/appointments/{appointment_id}/reminder")
async def send_appointment_reminder(
    appointment_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    appointment = await db.scalar(
        select(LeadAppointment).where(
            LeadAppointment.id == appointment_id,
            LeadAppointment.tenant_id == current_user.tenant_id,
        )
    )
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    lead = await db.scalar(
        select(Lead).where(
            Lead.id == appointment.lead_id, Lead.tenant_id == current_user.tenant_id
        )
    )
    contact = (
        await db.scalar(
            select(Contact).where(
                Contact.id == lead.contact_id,
                Contact.tenant_id == current_user.tenant_id,
            )
        )
        if lead
        else None
    )
    consent = await db.scalar(
        select(LeadChannelConsent).where(
            LeadChannelConsent.tenant_id == current_user.tenant_id,
            LeadChannelConsent.lead_id == appointment.lead_id,
        )
    )
    if (
        not contact
        or not contact.email
        or not consent
        or not consent.email_allowed
        or consent.revoked_at
    ):
        raise HTTPException(403, "Appointment reminder email is not consented")
    result = await email_service.send_email(
        [contact.email],
        "Your LawHand consultation reminder",
        f"<p>Your consultation is scheduled for {escape(appointment.start_at.isoformat())}.</p>",
        f"Your consultation is scheduled for {appointment.start_at.isoformat()}.",
    )
    appointment.reminder_status = (
        "sent" if result == EmailDeliveryResult.SENT else "failed"
    )
    db.add(
        LeadFunnelEvent(
            tenant_id=appointment.tenant_id,
            lead_id=appointment.lead_id,
            event_type="appointment_reminder_sent"
            if result == EmailDeliveryResult.SENT
            else "appointment_reminder_failed",
            source=lead.source if lead else None,
            metadata_json={"delivery": str(result)},
        )
    )
    await db.commit()
    if result != EmailDeliveryResult.SENT:
        raise HTTPException(
            503, "Email provider did not accept the appointment reminder"
        )
    return {
        "appointment_id": str(appointment.id),
        "reminder_status": appointment.reminder_status,
    }


@staff.post("/leads/{lead_id}/recover")
async def recover_lead(
    lead_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    lead = await db.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == current_user.tenant_id)
    )
    if not lead:
        raise HTTPException(404, "Lead not found")
    db.add(
        LeadFunnelEvent(
            tenant_id=lead.tenant_id,
            lead_id=lead.id,
            event_type="abandonment_recovery_reviewed",
            source=lead.source,
            metadata_json={"actor_user_id": str(current_user.id)},
        )
    )
    await db.commit()
    return {
        "lead_id": str(lead.id),
        "status": "recovery_reviewed",
        "requires_authored_follow_up": True,
    }


router.include_router(staff)
router.include_router(public)
