"""Durable intake: selected requirements, explicit delivery claims and timed staff work."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session_maker, set_tenant_context
from app.models.client_portal import ClientPortalInvite
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.configurable_workflow import MatterWorkflowRun
from app.models.conversion_loop import LeadChannelConsent
from app.models.matter_document import MatterDocument
from app.models.matter_intake import MatterIntake
from app.models.plugin import Matter, MatterEvent
from app.models.signature import SignatureRequest, SignatureSigner
from app.models.sms import SmsMessage
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User
from app.services.connected_mail import send_client_email
from app.services.email import email_service, render_branded_email
from app.services.matter_access import can_access_matter
from app.services.sms_categories import disclosure_version, granted_categories
from app.services.matter_file_store import MatterFileStore
from app.services.rbac_service import get_user_capabilities
from app.services.sms import (
    SmsError,
    append_sms_consent_event,
    load_sms_consents,
    normalize_e164,
    send_sms,
)
from app.services.task_workflow import append_task_event, transition_task
from app.services.token_vault import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)
# The reviewed fee agreement travels behind a secure portal link, not as an
# email attachment, so it keeps the direct-upload ceiling either way.
MAX_AGREEMENT_BYTES = 20 * 1024 * 1024


def now():
    return datetime.now(timezone.utc)


def public_packet(packet, *, client=False):
    data = {
        "id": str(packet.id),
        "matter_id": str(packet.matter_id),
        "status": packet.status,
        "requirements": packet.requirements,
        "questions": packet.config["questions"],
        "answers": packet.answers,
        "sent_at": packet.sent_at,
        "completed_at": packet.completed_at,
        "scheduling_due_at": packet.completed_at + timedelta(hours=24)
        if packet.completed_at
        else None,
        "meeting": packet.meeting,
        "signature_id": str(packet.signature_id) if packet.signature_id else None,
        "signing_followup_due_at": packet.config.get("signing_followup_due_at"),
        # Deadlines are stated in the client's own timezone on both sides.
        "timezone": packet.config["timezone"],
    }
    if not client:
        data.update(delivery=packet.delivery, owner_id=str(packet.owner_id))
        data["proposed_changes"] = packet.proposed_changes or {}
    else:
        data["requirements"] = {
            name: {
                key: value
                for key, value in requirement.items()
                if key
                in (
                    "completed",
                    "completed_at",
                    "sent_at",
                    "label",
                    "kind",
                    "document_id",
                    "signature_id",
                    "required",
                    "submitted_document_id",
                    "submitted_at",
                    "declined",
                    "declined_at",
                    "decline_reason",
                    "due_at",
                )
            }
            for name, requirement in packet.requirements.items()
        }
    return data


async def get_packet(db, tenant_id, matter_id, *, lock=False):
    if lock:
        await db.scalar(
            select(Matter.id)
            .where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
            .with_for_update()
        )
    statement = select(MatterIntake).where(
        MatterIntake.tenant_id == tenant_id, MatterIntake.matter_id == matter_id
    )
    return await db.scalar(
        statement.with_for_update().execution_options(populate_existing=True)
        if lock
        else statement
    )


async def store_file(tenant_id, matter, filename, content, content_type):
    # OAuth refresh may commit. Keep it out of the locked intake transaction.
    async with async_session_maker() as storage_db:
        await set_tenant_context(storage_db, str(tenant_id))
        return await MatterFileStore().store_matter_file_result(
            db=storage_db,
            tenant_id=str(tenant_id),
            matter_slug=matter.slug,
            category="intake",
            filename=filename,
            content=content,
            content_type=content_type,
            matter_cloud_folder=matter.cloud_folder,
        )


def due_iso(value):
    return value.astimezone(timezone.utc).isoformat() if value else None


def plan_signing_placements(signature, content, *, signer_name, placements):
    """Decide where the client signs, the same way the E-Signature panel does.

    Intake sends every signature request to a single ``signer`` role, so staff
    placements reviewed on the PDF are adopted when they exist and the plan
    otherwise finds (or falls back to) a signature line on the document.
    Invalid staff placements are not fatal here: the plan still guarantees a
    signature placement, and the reviewer's geometry is simply dropped.
    """
    from types import SimpleNamespace

    from app.services.esign.placement import PlacementError
    from app.services.esign.plan import plan_request_placements

    signers = [SimpleNamespace(name=signer_name, role="signer", sign_order=0)]
    usable = [
        {**item, "role": "signer"} for item in placements if isinstance(item, dict)
    ]
    try:
        plan_request_placements(signature, content, signers=signers, placements=usable)
    except PlacementError:
        plan_request_placements(signature, content, signers=signers, placements=[])


def requirement_label(key, requirement):
    if requirement.get("label"):
        return requirement["label"]
    return "Fee agreement" if key == "fee_agreement" else key.replace("_", " ")


def queue(packet, kind):
    delivery = dict(packet.delivery)
    for channel in packet.config["channels"]:
        key = f"{kind}:{channel}"
        delivery.setdefault(key, {"state": "queued", "attempt": 0})
    packet.delivery = delivery


def event(db, packet, title, content, *, event_type="intake"):
    db.add(
        MatterEvent(
            tenant_id=packet.tenant_id,
            matter_id=packet.matter_id,
            event_type=event_type,
            title=title,
            content=content,
            note_type="system",
            created_by=packet.created_by,
        )
    )


async def set_intake_stage(db, matter, stage):
    # An applied firm workflow owns its stage; intake still has independent state.
    managed = await db.scalar(
        select(MatterWorkflowRun.id)
        .where(
            MatterWorkflowRun.tenant_id == matter.tenant_id,
            MatterWorkflowRun.matter_id == matter.id,
            MatterWorkflowRun.status.in_(("applied", "compensation_required")),
        )
        .limit(1)
    )
    if managed is None:
        matter.stage = stage


def followup_task_description(due, timezone_name):
    """Readable follow-up text for a staff task.

    The task row already renders the due date, so the description must not
    leak the machine ``isoformat()`` (a raw timestamp is not user-facing).
    """
    local = due.astimezone(ZoneInfo(timezone_name))
    return (
        f"Intake action due {local.strftime('%b %d, %Y %I:%M %p')}. "
        "Client meeting options: conference call or in-person."
    )


async def ensure_task(db, packet, kind, title, due):
    task_id = uuid.uuid5(packet.id, kind)
    task = await db.scalar(
        select(Task)
        .where(Task.id == task_id, Task.tenant_id == packet.tenant_id)
        .with_for_update()
    )
    if task is None:
        owner = await db.scalar(
            select(User).where(
                User.id == packet.owner_id,
                User.tenant_id == packet.tenant_id,
                User.is_active.is_(True),
            )
        )
        owner_id = None
        if owner and await can_access_matter(
            db,
            tenant_id=packet.tenant_id,
            user_id=owner.id,
            is_admin=owner.role == "admin",
            matter_id=packet.matter_id,
        ):
            owner_id = owner.id
        local = due.astimezone(ZoneInfo(packet.config["timezone"]))
        task = Task(
            id=task_id,
            tenant_id=packet.tenant_id,
            matter_id=packet.matter_id,
            contact_id=packet.contact_id,
            title=title,
            description=followup_task_description(due, packet.config["timezone"]),
            task_type="follow_up",
            status="pending",
            priority="high",
            due_date=local.date(),
            due_time=local.time().replace(tzinfo=None),
            assigned_to_user_id=owner_id,
            created_by_user_id=packet.created_by,
            source="intake",
            external_ref=f"intake:{packet.id}:{kind}",
        )
        db.add(task)
        await db.flush()
        append_task_event(
            db,
            task,
            event_type="created",
            actor_user_id=packet.created_by,
            to_status="pending",
            note=title,
        )
    return task


async def close_task(db, packet, kind, reason):
    task = await db.scalar(
        select(Task)
        .where(
            Task.id == uuid.uuid5(packet.id, kind), Task.tenant_id == packet.tenant_id
        )
        .with_for_update()
    )
    if task and task.status not in ("completed", "cancelled"):
        transition_task(
            db,
            task,
            to_status="cancelled",
            actor_user_id=packet.created_by,
            reason=reason,
        )
        from app.services.task_notifications import task_calendar_user_id

        packet.config = {
            **packet.config,
            f"calendar_cleanup:{kind}": True,
            f"calendar_owner:{kind}": task_calendar_user_id(task),
        }


async def start_packet(db, user, matter, body, filename, content):
    request_hash = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode() + content
    ).hexdigest()
    packet = await get_packet(db, user.tenant_id, matter.id, lock=True)
    if packet:
        if packet.config["request_hash"] != request_hash:
            raise HTTPException(
                409, "This matter already has an intake packet; open its intake panel."
            )
        return packet
    if matter.is_closed or matter.status == "closed":
        raise HTTPException(409, "Reopen the matter before starting intake.")
    contact = await db.scalar(
        select(Contact)
        .where(
            Contact.id == matter.client_contact_id, Contact.tenant_id == user.tenant_id
        )
        .with_for_update()
    )
    if not contact:
        raise HTTPException(422, "Select a client before starting intake.")
    if not (contact.first_name or "").strip() or not (contact.last_name or "").strip():
        raise HTTPException(
            422, "Enter the client first and last name before starting intake."
        )
    if contact.email and contact.email.strip().lower() != str(body.email).lower():
        raise HTTPException(
            409, "The email differs from the selected client. Update the client first."
        )
    contact.email = str(body.email)
    owner_id = body.owner_id or user.id
    owner = await db.scalar(
        select(User).where(
            User.id == owner_id,
            User.tenant_id == user.tenant_id,
            User.is_active.is_(True),
        )
    )
    if not owner or not await can_access_matter(
        db,
        tenant_id=user.tenant_id,
        user_id=owner_id,
        is_admin=owner.role == "admin",
        matter_id=matter.id,
    ):
        raise HTTPException(
            422, "Assign intake to an active staff member with matter access."
        )
    if content and (
        not content.startswith(b"%PDF-") or len(content) > MAX_AGREEMENT_BYTES
    ):
        raise HTTPException(
            422, "Upload the reviewed fee agreement as a PDF up to 20 MiB."
        )
    packet_id = uuid.uuid4()
    if "sms" in body.channels:
        existing = await load_sms_consents(db, user.tenant_id, contact.id, lock=True)
        if not existing:
            if "manage_intake" not in await get_user_capabilities(db, user.id):
                raise HTTPException(
                    403, "Recording new SMS permission requires manage_intake access."
                )
            if not body.sms_permission_verified:
                raise HTTPException(
                    422,
                    "Record verified SMS permission before selecting text delivery.",
                )
            try:
                mobile = normalize_e164(contact.phone)
            except SmsError as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc
            lead = Lead(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                contact_id=contact.id,
                matter_id=matter.id,
                status="matter_opened",
                source="existing_client",
                created_by_user_id=user.id,
            )
            db.add(lead)
            await db.flush()
            consent = LeadChannelConsent(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                lead_id=lead.id,
                email_allowed=True,
                sms_allowed=True,
                sms_status="active",
                phone_verified=True,
                mobile_e164=mobile,
                consented_at=now(),
                consent_source="staff_recorded_intake",
                disclosure_version=disclosure_version(
                    case_updates=body.sms_case_updates_verified
                ),
                consent_timezone=body.timezone,
                quiet_hours_start="20:00",
                quiet_hours_end="08:00",
                allowed_categories=granted_categories(
                    case_updates=body.sms_case_updates_verified
                ),
                consent_language="en",
            )
            db.add(consent)
            await db.flush()
            append_sms_consent_event(
                db,
                consent=consent,
                contact_id=contact.id,
                action="staff_grant",
                actor_type="tenant_user",
                actor_user_id=user.id,
            )
            contact.sms_opt_in = True
            contact.sms_opt_in_at = consent.consented_at
    document = None
    signature = None
    # An agreement is optional: a packet may carry only the questionnaire, the
    # intake form, or requested uploads. The portal invite below is created
    # either way, so the client can always reach that paperwork.
    if content:
        if body.agreement_document_id:
            document = await db.scalar(
                select(MatterDocument).where(
                    MatterDocument.id == body.agreement_document_id,
                    MatterDocument.tenant_id == user.tenant_id,
                    MatterDocument.matter_id == matter.id,
                )
            )
            if document is None:
                raise HTTPException(404, "Fee agreement not found")
            # The packet entitles its own recipient to read this agreement. A
            # matter-wide visibility bit would hand it to every other live invite.
        else:
            stored = await store_file(
                user.tenant_id,
                matter,
                f"intake-{packet_id}.pdf",
                content,
                "application/pdf",
            )
            if not stored.succeeded:
                raise HTTPException(
                    503,
                    "Agreement storage is unavailable. Reconnect storage and retry.",
                )
            document = MatterDocument(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                matter_id=matter.id,
                uploaded_by_user_id=user.id,
                filename=filename[:250],
                content_type="application/pdf",
                file_size=len(content),
                document_category="contract",
                storage_path=stored.storage_path,
                storage_provider=stored.provider,
                storage_backend=stored.backend,
                provider_object_id=stored.provider_item_id,
                provider_drive_id=stored.drive_id,
                provider_parent_id=stored.parent_id,
            )
            db.add(document)
            await db.flush()
        signature = SignatureRequest(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            matter_id=matter.id,
            document_id=document.id,
            status="sent",
            provider="internal",
            source_document_sha256=hashlib.sha256(content).hexdigest(),
            source_document_size=len(content),
            source_document_filename=document.filename,
            created_by_user_id=user.id,
            sent_at=now(),
            expires_at=now() + timedelta(days=30),
            reminders={},
        )
        plan_signing_placements(
            signature,
            content,
            signer_name=contact.display_name or str(body.email),
            placements=document.positioned_fields or [],
        )
        db.add(signature)
        await db.flush()
        db.add(
            SignatureSigner(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                request_id=signature.id,
                contact_id=contact.id,
                name=contact.display_name or str(body.email),
                email=str(body.email),
                role="signer",
                sign_order=0,
                status="pending",
            )
        )
    token = secrets.token_urlsafe(32)
    invite = ClientPortalInvite(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        contact_id=contact.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        email=str(body.email),
        expires_at=now() + timedelta(days=30),
        created_by_user_id=user.id,
    )
    db.add(invite)
    await db.flush()
    packet = MatterIntake(
        id=packet_id,
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        contact_id=contact.id,
        owner_id=owner_id,
        created_by=user.id,
        signature_id=signature.id if signature else None,
        invite_id=invite.id,
        encrypted_invite=encrypt_token(token),
        status="awaiting_documents",
        config={
            **body.model_dump(mode="json"),
            "request_hash": request_hash,
            # An agreement-confirming signature only exists when one was sent;
            # without it the client gets full portal access from the first
            # message instead of waiting on a signing milestone.
            "portal_after_signing": body.portal_after_signing and signature is not None,
            **(
                {"source_sha256": signature.source_document_sha256} if signature else {}
            ),
        },
        requirements={
            **(
                {
                    "fee_agreement": {
                        "completed": False,
                        "due_at": due_iso(body.agreement_due_at),
                    }
                }
                if signature
                else {}
            ),
            "questionnaire": {
                "completed": not body.include_questionnaire,
                "required": body.include_questionnaire,
                "completed_at": None,
                "due_at": due_iso(body.questionnaire_due_at),
            },
        },
        answers={},
        delivery={},
    )
    for upload in body.upload_requirements:
        packet.requirements[upload.key] = {
            "completed": False,
            "kind": "upload",
            "label": upload.label,
            "required": upload.required,
            "due_at": due_iso(upload.due_at),
        }
    for selection in body.selected_documents:
        from app.services.matter_mail_attachments import reviewed_attachment

        attachment, digest = await reviewed_attachment(
            db, user.tenant_id, matter.id, selection.document_id
        )
        selected_doc = await db.scalar(
            select(MatterDocument).where(
                MatterDocument.id == selection.document_id,
                MatterDocument.tenant_id == user.tenant_id,
                MatterDocument.matter_id == matter.id,
            )
        )
        if selected_doc is None:
            raise HTTPException(404, "Selected document not found")
        signature_id = None
        if selection.requires_signature:
            if not attachment.content.startswith(b"%PDF-"):
                raise HTTPException(422, "Signature documents must be reviewed PDFs")
            extra = SignatureRequest(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                matter_id=matter.id,
                document_id=selection.document_id,
                status="sent",
                provider="internal",
                source_document_sha256=digest,
                source_document_size=len(attachment.content),
                source_document_filename=attachment.filename,
                created_by_user_id=user.id,
                sent_at=now(),
                expires_at=now() + timedelta(days=30),
                reminders={},
            )
            plan_signing_placements(
                extra,
                attachment.content,
                signer_name=contact.display_name or str(body.email),
                placements=selected_doc.positioned_fields or [],
            )
            db.add(extra)
            await db.flush()
            db.add(
                SignatureSigner(
                    id=uuid.uuid4(),
                    tenant_id=user.tenant_id,
                    request_id=extra.id,
                    contact_id=contact.id,
                    name=contact.display_name or str(body.email),
                    email=str(body.email),
                    role="signer",
                    sign_order=0,
                    status="pending",
                )
            )
            signature_id = str(extra.id)
        packet.requirements[f"document_{selection.document_id.hex}"] = {
            "completed": False,
            "required": True,
            "kind": "signature" if signature_id else "document",
            "label": selection.label,
            "document_id": str(selection.document_id),
            "signature_id": signature_id,
            "source_sha256": digest,
            "due_at": due_iso(selection.due_at),
        }
    queue(packet, "welcome")
    db.add(packet)
    matter.portal_enabled = True
    await set_intake_stage(db, matter, "Intake / Awaiting Documents")
    event(
        db,
        packet,
        "Intake started",
        "Client paperwork requested; portal delivery queued.",
    )
    await db.commit()
    return packet


async def cancel_packet(db, packet, reason):
    packet.status = "cancelled"
    dated = [
        f"due:{key}"
        for key, requirement in packet.requirements.items()
        if requirement.get("due_at")
    ]
    for kind in ("documents", "scheduling", "delivery", "signed", *dated):
        await close_task(db, packet, kind, reason)
    signature = None
    if packet.signature_id:
        signature = await db.scalar(
            select(SignatureRequest).where(
                SignatureRequest.id == packet.signature_id,
                SignatureRequest.tenant_id == packet.tenant_id,
            )
        )
    if signature and signature.status not in ("completed", "voided"):
        signature.status = "voided"
        signature.voided_at = now()
        signature.void_reason = reason
    for requirement in packet.requirements.values():
        extra_id = (
            requirement.get("signature_id")
            if requirement.get("kind") == "signature"
            else None
        )
        if extra_id:
            extra = await db.scalar(
                select(SignatureRequest).where(
                    SignatureRequest.id == uuid.UUID(extra_id),
                    SignatureRequest.tenant_id == packet.tenant_id,
                )
            )
            if extra and extra.status not in ("completed", "voided"):
                extra.status, extra.voided_at, extra.void_reason = (
                    "voided",
                    now(),
                    reason,
                )
    packet.delivery = {
        key: {**state, "state": "cancelled"}
        if state["state"] in ("queued", "blocked", "failed")
        else state
        for key, state in packet.delivery.items()
    }


async def mirror_submissions(db, packet):
    """Copy each open request's uploaded-copy state onto its requirement."""
    targets = []
    if packet.signature_id and packet.requirements.get("fee_agreement"):
        targets.append(("fee_agreement", packet.signature_id))
    for key, requirement in packet.requirements.items():
        if requirement.get("kind") == "signature" and requirement.get("signature_id"):
            try:
                targets.append((key, uuid.UUID(str(requirement["signature_id"]))))
            except (TypeError, ValueError):
                continue
    for key, request_id in targets:
        requirement = packet.requirements.get(key)
        if not requirement or requirement.get("completed"):
            continue
        request = await db.scalar(
            select(SignatureRequest).where(
                SignatureRequest.id == request_id,
                SignatureRequest.tenant_id == packet.tenant_id,
                SignatureRequest.matter_id == packet.matter_id,
            )
        )
        if request is None:
            continue
        submitted_id = (
            str(request.submitted_document_id)
            if request.submitted_document_id and request.status != "completed"
            else None
        )
        submitted_at = (
            request.submitted_at.isoformat()
            if submitted_id and request.submitted_at
            else None
        )
        if (
            requirement.get("submitted_document_id") == submitted_id
            and requirement.get("submitted_at") == submitted_at
        ):
            continue
        updated = {
            key_: value
            for key_, value in requirement.items()
            if key_ not in ("submitted_document_id", "submitted_at")
        }
        if submitted_id:
            updated["submitted_document_id"] = submitted_id
            updated["submitted_at"] = submitted_at
        packet.requirements = {**packet.requirements, key: updated}


async def reconcile(db, packet):
    if packet.status == "cancelled":
        return
    matter = await db.scalar(
        select(Matter).where(
            Matter.id == packet.matter_id, Matter.tenant_id == packet.tenant_id
        )
    )
    if not matter or matter.is_closed or matter.status == "closed":
        await cancel_packet(db, packet, "Matter closed")
        return
    if packet.status == "scheduled":
        return
    signature = None
    if packet.signature_id:
        signature = await db.scalar(
            select(SignatureRequest).where(
                SignatureRequest.id == packet.signature_id,
                SignatureRequest.tenant_id == packet.tenant_id,
                SignatureRequest.matter_id == packet.matter_id,
            )
        )
    if (
        signature
        and signature.status == "completed"
        and signature.completed_at
        and signature.source_document_sha256 == packet.config.get("source_sha256")
        and signature.completion_artifact_sha256
    ):
        try:
            artifact_id = uuid.UUID(signature.provider_envelope_id or "")
        except ValueError:
            artifact_id = None
        artifact = (
            await db.scalar(
                select(MatterDocument.id).where(
                    MatterDocument.id == artifact_id,
                    MatterDocument.tenant_id == packet.tenant_id,
                    MatterDocument.matter_id == packet.matter_id,
                )
            )
            if artifact_id
            else None
        )
        fee_requirement = packet.requirements.get("fee_agreement")
        if artifact and fee_requirement and not fee_requirement["completed"]:
            packet.requirements = {
                **packet.requirements,
                "fee_agreement": {
                    # Keep the requirement's own fields, its due date among
                    # them, so its follow-up task can be closed on completion.
                    **fee_requirement,
                    "completed": True,
                    "completed_at": signature.completed_at.isoformat(),
                    "evidence": "signature_acknowledgment_certificate",
                    "signature_id": str(signature.id),
                },
            }
    for key, requirement in list(packet.requirements.items()):
        if requirement.get("kind") != "signature" or requirement.get("completed"):
            continue
        extra = await db.scalar(
            select(SignatureRequest).where(
                SignatureRequest.id == uuid.UUID(requirement["signature_id"]),
                SignatureRequest.tenant_id == packet.tenant_id,
                SignatureRequest.matter_id == packet.matter_id,
            )
        )
        if (
            extra
            and extra.status == "completed"
            and extra.completed_at
            and extra.completion_artifact_sha256
            and extra.source_document_sha256 == requirement["source_sha256"]
        ):
            try:
                artifact_id = uuid.UUID(extra.provider_envelope_id or "")
            except ValueError:
                continue
            artifact = await db.scalar(
                select(MatterDocument.id).where(
                    MatterDocument.id == artifact_id,
                    MatterDocument.tenant_id == packet.tenant_id,
                    MatterDocument.matter_id == packet.matter_id,
                )
            )
            if not artifact:
                continue
            packet.requirements = {
                **packet.requirements,
                key: {
                    **requirement,
                    "completed": True,
                    "completed_at": extra.completed_at.isoformat(),
                },
            }
    # An uploaded signed copy awaiting staff review shows on the requirement
    # so both the firm strip and the client checklist can say "Awaiting
    # review"; it is cleared again when staff return the copy.
    await mirror_submissions(db, packet)
    # A declined acknowledgment is terminal: surface it on the matter timeline,
    # stop chasing the due date, and raise a staff task to revise and resend.
    # The packet stays open because the required signature is still outstanding,
    # but the firm is no longer waiting on a request the client already refused.
    declined: list[tuple[str, SignatureRequest]] = []
    main_request = (
        await db.scalar(
            select(SignatureRequest).where(
                SignatureRequest.id == packet.signature_id,
                SignatureRequest.tenant_id == packet.tenant_id,
                SignatureRequest.matter_id == packet.matter_id,
            )
        )
        if packet.signature_id
        else None
    )
    if main_request is not None and main_request.status == "declined":
        declined.append(("fee_agreement", main_request))
    for key, requirement in list(packet.requirements.items()):
        if (
            requirement.get("kind") != "signature"
            or not requirement.get("signature_id")
            or requirement.get("declined")
        ):
            continue
        try:
            extra_id = uuid.UUID(str(requirement["signature_id"]))
        except (TypeError, ValueError):
            continue
        extra = await db.scalar(
            select(SignatureRequest).where(
                SignatureRequest.id == extra_id,
                SignatureRequest.tenant_id == packet.tenant_id,
                SignatureRequest.matter_id == packet.matter_id,
            )
        )
        if extra is not None and extra.status == "declined":
            declined.append((key, extra))
    for key, request in declined:
        requirement = packet.requirements[key]
        if requirement.get("declined"):
            continue
        label = requirement_label(key, requirement)
        declined_at = request.declined_at or now()
        reason = (request.decline_reason or "").strip()
        packet.requirements = {
            **packet.requirements,
            key: {
                **requirement,
                "declined": True,
                "declined_at": declined_at.isoformat(),
                "decline_reason": reason or None,
            },
        }
        event(
            db,
            packet,
            f"{label} declined",
            f"The client declined to sign {label}."
            + (f" Reason: {reason}." if reason else "")
            + " Revise the document and resend it for signature.",
            event_type="signature",
        )
        await ensure_task(
            db,
            packet,
            f"declined:{key}",
            f"{label} declined — revise and resend",
            now() + timedelta(days=1),
        )
    agreement = packet.requirements.get("fee_agreement")
    if (
        agreement
        and packet.config.get("portal_after_signing")
        and agreement["completed"]
        and not packet.config.get("signing_followup_due_at")
    ):
        signed_at = datetime.fromisoformat(agreement["completed_at"])
        due = signed_at + timedelta(hours=24)
        await ensure_task(
            db, packet, "signed", "Fee agreement signed — follow up with client", due
        )
        packet.config = {**packet.config, "signing_followup_due_at": due.isoformat()}
        queue(packet, "signed")
        event(
            db,
            packet,
            "Fee agreement signed",
            "Portal delivery queued. Follow up with the client within 24 hours.",
        )
    elif (
        agreement is None
        and packet.sent_at
        and not packet.config.get("signing_followup_due_at")
    ):
        # No fee agreement to wait on: the clock starts when the first message
        # goes out, so the firm still follows up with the client within 24 hours.
        due = packet.sent_at + timedelta(hours=24)
        await ensure_task(
            db, packet, "signed", "Paperwork sent — follow up with client", due
        )
        packet.config = {**packet.config, "signing_followup_due_at": due.isoformat()}
        event(
            db,
            packet,
            "Client paperwork sent",
            "No fee agreement included. Follow up with the client within 24 hours.",
        )
    # Each dated requirement carries its own assigned follow-up. The task is
    # keyed by requirement, so a reconcile pass never duplicates it, and the
    # task closes as soon as the client's paperwork lands.
    for key, requirement in list(packet.requirements.items()):
        due_at = requirement.get("due_at")
        if not due_at:
            continue
        label = requirement_label(key, requirement)
        if requirement.get("completed"):
            await close_task(db, packet, f"due:{key}", f"{label} received")
        elif requirement.get("declined"):
            await close_task(db, packet, f"due:{key}", f"{label} declined")
        else:
            await ensure_task(
                db,
                packet,
                f"due:{key}",
                f"{label} due from client",
                datetime.fromisoformat(due_at),
            )

    required_keys = [
        key
        for key, requirement in packet.requirements.items()
        if requirement.get("required", True)
    ]
    complete = all(packet.requirements[key]["completed"] for key in required_keys)
    if complete and packet.completed_at is None:
        times = [
            datetime.fromisoformat(packet.requirements[k]["completed_at"])
            for k in required_keys
        ]
        packet.completed_at = max(times)
        packet.status = "documents_complete"
        await set_intake_stage(db, matter, "Intake / Schedule Initial Meeting")
        await close_task(
            db, packet, "documents", "All required intake documents completed"
        )
        await ensure_task(
            db,
            packet,
            "scheduling",
            "Intake complete — contact client to schedule initial meeting",
            packet.completed_at + timedelta(hours=24),
        )
        event(
            db,
            packet,
            "Intake documents complete",
            "All required intake documents complete. Schedule the initial meeting within 24 hours.",
        )
        queue(packet, "complete")
    elif not complete and packet.sent_at:
        await ensure_task(
            db,
            packet,
            "documents",
            "Follow up on outstanding intake documents",
            packet.sent_at + timedelta(days=7),
        )
        if now() >= packet.sent_at + timedelta(days=7):
            queue(packet, "reminder")


def requested_upload_labels(packet, *, outstanding_only=False):
    """Client-facing labels for the records the packet asks the client to upload."""
    labels = []
    for key, item in packet.requirements.items():
        if not key.startswith("upload_"):
            continue
        if outstanding_only and item.get("completed"):
            continue
        labels.append(item.get("label") or key.replace("_", " "))
    return labels


@dataclass(frozen=True)
class ClientMessage:
    """One client-facing intake notification across both delivery channels.

    The same event reads differently by channel: email gets the branded shell
    and the full checklist, while the text stays short enough to read on a
    lock screen. Keeping them on one object stops a rich email body from
    leaking into an SMS.
    """

    subject: str
    text: str
    html: str
    sms: str


def _client_first_name(name):
    if not name:
        return None
    stripped = name.strip()
    return stripped.split()[0] if stripped else None


def _firm_display(brand):
    name = (brand or {}).get("firm_name")
    return name.strip() if name and name.strip() else "Your legal team"


def _html_bullets(items):
    if not items:
        return ""
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _text_bullets(items):
    return "\n".join(f"- {item}" for item in items)


def _contact_line(brand):
    bits = []
    if (brand or {}).get("firm_phone"):
        bits.append(f"call {brand['firm_phone']}")
    if (brand or {}).get("firm_email"):
        bits.append(f"email {brand['firm_email']}")
    return "Questions? " + " or ".join(bits) + "." if bits else ""


_HEADER_SUBTITLES = {
    "welcome": "You have secure paperwork waiting",
    "signed": "Your portal is ready",
    "reminder": "Your paperwork is waiting",
    "meeting": "Your meeting is confirmed",
    "complete": "Your intake is complete",
}


def render_client_message(
    kind,
    url,
    *,
    labels=None,
    uploads=None,
    missing=None,
    has_fee_agreement=False,
    portal_after_signing=False,
    brand=None,
    client_name=None,
    expires_at=None,
):
    """Build the subject, email and text copy for one intake notification.

    Deliberately packet-free: the preview endpoint calls it before a packet
    exists and delivery calls it after, so what staff preview is the exact
    copy that gets sent. ``email.render_branded_email`` supplies only the
    chrome; every word of the message lives here.
    """
    labels = list(labels or [])
    uploads = list(uploads or [])
    missing = list(missing or [])
    brand = brand or {}
    firm = _firm_display(brand)
    has_firm_name = firm != "Your legal team"
    first_name = _client_first_name(client_name)
    safe_url = escape(url, quote=True)
    header_title = escape(firm) if has_firm_name else "Client Portal"

    sections = []  # (heading, [items]) rendered under the intro
    note_text = ""
    if kind == "welcome":
        subject = (
            f"{firm}: Please review and complete your paperwork"
            if has_firm_name
            else "Please review and complete your paperwork"
        )
        if labels:
            intro = (
                f"{firm} has prepared the following paperwork for you to "
                "review and complete:"
            )
        elif uploads:
            intro = f"{firm} has asked you to provide the following:"
        else:
            intro = f"{firm} has paperwork for you in your secure client portal:"
        if uploads:
            sections.append(("Please have these records ready to upload:", uploads))
        if portal_after_signing and has_fee_agreement:
            # The link in this message opens the portal; signing is what
            # unlocks the rest of it. No second link is coming, so say what
            # signing actually does instead of promising a follow-up email.
            note_text = (
                "Signing your fee agreement also opens your full client portal — "
                "secure messages, documents, and billing."
            )
        sms = (
            f"{firm}: Your paperwork is ready to review and complete in your "
            f"secure portal: {url}"
        )
    elif kind == "signed":
        subject = "Your client portal is ready"
        if uploads:
            intro = (
                "Your fee agreement signature was received. Please complete the "
                "remaining paperwork and upload the requested records:"
            )
            sections.append(("Please have these records ready to upload:", uploads))
        else:
            intro = (
                "Your fee agreement signature was received. Complete any remaining "
                "paperwork in your secure client portal:"
            )
        sms = (
            f"{firm}: Your fee agreement was received. Continue in your secure "
            f"portal: {url}"
        )
    elif kind == "reminder":
        subject = "Your intake needs attention"
        intro = "Please complete the following in your secure client portal:"
        if missing:
            sections.append(("Still needed:", missing))
        sms = (
            f"{firm}: Reminder — your paperwork is waiting in your secure portal: {url}"
        )
    elif kind == "meeting":
        subject = "Initial meeting confirmed"
        intro = (
            "Your initial meeting details are available in your secure client portal:"
        )
        sms = (
            f"{firm}: Your initial meeting is confirmed. Details in your secure "
            f"portal: {url}"
        )
    else:  # complete
        subject = "Your intake documents are complete"
        intro = (
            "Thank you. Your legal team will contact you to arrange a conference "
            "call or in-person meeting. Your secure portal:"
        )
        sms = f"{firm}: Your intake is complete. We'll be in touch to schedule. {url}"

    expiry_text = (
        f"It expires on {expires_at.strftime('%B %d, %Y')}."
        if expires_at is not None
        else "It expires after 30 days."
    )
    contact_text = _contact_line(brand)

    html_parts = []
    if first_name:
        html_parts.append(f"<p>Hi {escape(first_name)},</p>")
    html_parts.append(f"<p>{escape(intro)}</p>")
    html_parts.append(_html_bullets(labels))
    for heading, items in sections:
        html_parts.append(f"<p>{escape(heading)}</p>")
        html_parts.append(_html_bullets(items))
    html_parts.append(
        '<p style="margin:24px 0;">'
        f'<a href="{safe_url}" style="background:#0f2d5e;color:#ffffff;'
        "text-decoration:none;padding:12px 24px;border-radius:6px;"
        'font-weight:bold;display:inline-block;">Open Secure Client Portal</a></p>'
    )
    html_parts.append(
        '<p style="font-size:12px;color:#888;">If the button doesn\'t work, '
        f"copy and paste this link into your browser:<br/>{safe_url}</p>"
    )
    if note_text:
        html_parts.append(
            f'<p style="font-size:12px;color:#888;">{escape(note_text)}</p>'
        )
    html_parts.append(
        '<p style="font-size:12px;color:#888;">This link is unique to you. '
        f"Do not forward it. {escape(expiry_text)}</p>"
    )
    if contact_text:
        html_parts.append(f"<p>{escape(contact_text)}</p>")
    content_html = (
        f'<div class="header"><h1>{header_title}</h1>'
        f"<p>{escape(_HEADER_SUBTITLES.get(kind, 'Your intake is complete'))}</p></div>"
        f'<div class="body">{"".join(p for p in html_parts if p)}</div>'
    )

    text_parts = [intro]
    if labels:
        text_parts.append(_text_bullets(labels))
    for heading, items in sections:
        text_parts.append(heading)
        text_parts.append(_text_bullets(items))
    text_parts.append("Open your secure client portal:")
    text_parts.append(url)
    text_parts.append(expiry_text)
    if note_text:
        text_parts.append(note_text)
    if contact_text:
        text_parts.append(contact_text)
    text_parts.append(f"Thank you,\n{firm}")
    text = "\n\n".join(part for part in text_parts if part)
    if first_name:
        text = f"Hi {first_name},\n\n{text}"

    return ClientMessage(
        subject=subject,
        text=text,
        html=render_branded_email(content_html),
        sms=sms,
    )


def message(packet, kind, url, *, brand=None, client_name=None, expires_at=None):
    """Render a notification from packet state.

    Thin adapter over :func:`render_client_message` so delivery and the unit
    tests keep a single packet-shaped entry point.
    """
    if kind == "signed":
        uploads = requested_upload_labels(packet, outstanding_only=True)
        return render_client_message(
            kind,
            url,
            uploads=uploads,
            brand=brand,
            client_name=client_name,
            expires_at=expires_at,
        )
    if kind == "welcome":
        has_fee_agreement = "fee_agreement" in packet.requirements
        # The fee agreement is listed whenever it is part of the packet, not
        # only when the portal waits on signing, so a checkbox never hides a
        # document the client still has to sign.
        labels = [
            *(["Fee agreement"] if has_fee_agreement else []),
            *[item["label"] for item in packet.config.get("selected_documents", [])],
        ]
        return render_client_message(
            kind,
            url,
            labels=labels,
            uploads=requested_upload_labels(packet),
            has_fee_agreement=has_fee_agreement,
            portal_after_signing=bool(packet.config.get("portal_after_signing")),
            brand=brand,
            client_name=client_name,
            expires_at=expires_at,
        )
    if kind == "reminder":
        missing = [
            item.get("label", key.replace("_", " "))
            for key, item in packet.requirements.items()
            if item.get("required", True) and not item["completed"]
        ]
        return render_client_message(
            kind,
            url,
            missing=missing,
            brand=brand,
            client_name=client_name,
            expires_at=expires_at,
        )
    return render_client_message(
        kind,
        url,
        brand=brand,
        client_name=client_name,
        expires_at=expires_at,
    )


async def deliver(db, packet, key):
    state = packet.delivery[key]
    kind, channel = key.split(":")
    if state["state"] != "queued" or packet.status == "cancelled":
        return
    if state.get("not_before") and datetime.fromisoformat(state["not_before"]) > now():
        return
    if kind in ("welcome", "reminder") and packet.completed_at:
        packet.delivery = {**packet.delivery, key: {**state, "state": "cancelled"}}
        await db.commit()
        return
    actor = await db.scalar(
        select(User).where(
            User.id == packet.created_by,
            User.tenant_id == packet.tenant_id,
            User.is_active.is_(True),
        )
    )
    contact = await db.scalar(
        select(Contact).where(
            Contact.id == packet.contact_id, Contact.tenant_id == packet.tenant_id
        )
    )
    invite = await db.scalar(
        select(ClientPortalInvite).where(
            ClientPortalInvite.id == packet.invite_id,
            ClientPortalInvite.tenant_id == packet.tenant_id,
        )
    )
    allowed = (
        actor
        and "manage_matters" in await get_user_capabilities(db, actor.id)
        and await can_access_matter(
            db,
            tenant_id=packet.tenant_id,
            user_id=actor.id,
            is_admin=actor.role == "admin",
            matter_id=packet.matter_id,
        )
    )
    if (
        not allowed
        or not contact
        or (contact.email or "").lower() != packet.config["email"].lower()
        or not invite
        or invite.revoked
        or invite.expires_at <= now()
    ):
        packet.delivery = {
            **packet.delivery,
            key: {
                **state,
                "state": "blocked",
                "detail": "Check staff access, client contact and portal invitation.",
            },
        }
        await ensure_task(
            db, packet, "delivery", "Review intake notification delivery", now()
        )
        await db.commit()
        return
    packet_id, tenant_id, matter_id, actor_id, contact_id = (
        packet.id,
        packet.tenant_id,
        packet.matter_id,
        actor.id,
        contact.id,
    )
    # Branding is resolved here so the email identifies the firm the client
    # actually hired. Imported locally to avoid a service -> router cycle.
    from app.routers.firm import get_firm_branding

    tenant = await db.get(Tenant, tenant_id)
    brand = await get_firm_branding(db, tenant) if tenant else {}
    url = f"{get_settings().FRONTEND_URL.rstrip('/')}/portal/client/accept?token={decrypt_token(packet.encrypted_invite)}"
    rendered = message(
        packet,
        kind,
        url,
        brand=brand,
        client_name=contact.display_name,
        expires_at=invite.expires_at,
    )
    email = packet.config["email"]
    packet.delivery = {
        **packet.delivery,
        key: {**state, "state": "sending", "started_at": now().isoformat()},
    }
    await db.commit()  # durable claim precedes provider I/O and token refresh commits
    outcome = "unknown"
    provider = channel
    detail = "Check provider records before retrying an uncertain delivery."
    deferred = False
    sms_message_id = None
    try:
        await set_tenant_context(db, str(tenant_id))
        if channel == "email":
            result = await send_client_email(
                db,
                tenant_id=tenant_id,
                actor_user_id=actor_id,
                to=[email],
                subject=rendered.subject,
                html_body=rendered.html,
                text_body=rendered.text,
                smtp_service=email_service,
            )
            outcome = {"confirmed_sent": "sent", "not_attempted": "failed"}.get(
                result.delivery_certainty, "unknown"
            )
            provider = result.provider
            detail = getattr(result, "detail", "")
        else:
            result = await send_sms(
                db,
                tenant_id=tenant_id,
                user_id=actor_id,
                contact_id=contact_id,
                matter_id=matter_id,
                body=rendered.sms,
                category="intake",
                idempotency_key=f"intake:{packet_id}:{key}:{state['attempt']}",
            )
            sms_message_id = str(result.id) if getattr(result, "id", None) else None
            outcome = (
                "sent"
                if result.delivery_certainty in ("confirmed_sent", "provider_accepted")
                else "unknown"
            )
    except SmsError as exc:
        detail = str(exc)
        sms_message_id = str(exc.sms_message_id) if exc.sms_message_id else None
        outcome = "blocked" if exc.delivery_certainty == "not_attempted" else "unknown"
        deferred = (
            exc.code == "sms_quiet_hours" and exc.delivery_certainty == "not_attempted"
        )
    except Exception:
        logger.exception(
            "Intake delivery outcome requires review for packet %s", packet_id
        )
        await db.rollback()
    await set_tenant_context(db, str(tenant_id))
    packet = await get_packet(db, tenant_id, matter_id, lock=True)
    packet.delivery = {
        **packet.delivery,
        key: {
            **state,
            "state": outcome,
            "provider": provider,
            "sms_message_id": sms_message_id,
            "detail": detail if outcome != "sent" else "",
            "updated_at": now().isoformat(),
        },
    }
    if deferred:
        packet.delivery = {
            **packet.delivery,
            key: {
                "state": "queued",
                "attempt": state["attempt"] + 1,
                "not_before": (now() + timedelta(minutes=30)).isoformat(),
                "detail": "Waiting for permitted SMS hours",
            },
        }
    if outcome == "sent":
        if kind == "welcome" and packet.sent_at is None:
            packet.sent_at = now()
            packet.requirements = {
                name: {**requirement, "sent_at": packet.sent_at.isoformat()}
                for name, requirement in packet.requirements.items()
            }
        if channel == "email":
            # Bearer invitation links must not enter staff-visible correspondence logs.
            db.add(
                CommunicationLog(
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    contact_id=contact_id,
                    created_by_user_id=actor_id,
                    channel="email",
                    direction="outbound",
                    status="sent",
                    subject=rendered.subject,
                    body="Secure intake portal notification sent.",
                    external_ref=f"intake:{packet_id}:{key}:{state['attempt']}",
                )
            )
    elif not deferred:
        await ensure_task(
            db, packet, "delivery", "Review intake notification delivery", now()
        )
    await reconcile(db, packet)
    await db.commit()


async def process_packet(tenant_id, matter_id):
    async with async_session_maker() as db:
        await set_tenant_context(db, str(tenant_id))
        packet = await get_packet(db, tenant_id, matter_id, lock=True)
        if packet is None:
            return
        await reconcile(db, packet)
        # A crashed send is never automatically re-sent: provider acceptance may
        # have happened before the process stopped. Staff reconcile explicitly.
        delivery = dict(packet.delivery)
        for key, state in delivery.items():
            if state.get("sms_message_id"):
                sms = await db.scalar(
                    select(SmsMessage).where(
                        SmsMessage.id == uuid.UUID(state["sms_message_id"]),
                        SmsMessage.tenant_id == tenant_id,
                    )
                )
                if sms and sms.delivery_certainty in (
                    "provider_rejected",
                    "provider_failed_after_acceptance",
                ):
                    delivery[key] = {
                        **state,
                        "state": "failed",
                        "detail": "SMS provider reported unsuccessful delivery",
                    }
                    await ensure_task(
                        db,
                        packet,
                        "delivery",
                        "Review intake notification delivery",
                        now(),
                    )
                elif sms and sms.delivery_certainty == "confirmed_sent":
                    delivery[key] = {
                        **state,
                        "state": "sent",
                        "detail": "SMS delivery confirmed",
                    }
                    if key.startswith("welcome:") and packet.sent_at is None:
                        packet.sent_at = sms.last_event_at or now()
                        packet.requirements = {
                            name: {**requirement, "sent_at": packet.sent_at.isoformat()}
                            for name, requirement in packet.requirements.items()
                        }
            if state["state"] == "sending" and datetime.fromisoformat(
                state["started_at"]
            ) < now() - timedelta(minutes=10):
                delivery[key] = {**state, "state": "unknown"}
                await ensure_task(
                    db, packet, "delivery", "Review intake notification delivery", now()
                )
        packet.delivery = delivery
        await reconcile(db, packet)
        await db.commit()
        for key in delivery:
            await set_tenant_context(db, str(tenant_id))
            packet = await get_packet(db, tenant_id, matter_id, lock=True)
            await deliver(db, packet, key)
            await db.commit()
        # Existing task notifications project staff work into Microsoft/Google.
        from app.services.task_notifications import (
            notify_task_created,
            remove_task_from_calendars_now,
        )

        for kind in ("documents", "scheduling", "delivery", "signed"):
            await set_tenant_context(db, str(tenant_id))
            packet = await get_packet(db, tenant_id, matter_id, lock=True)
            cleanup = f"calendar_cleanup:{kind}"
            if packet.config.get(cleanup):
                task_id, owner_id = (
                    str(uuid.uuid5(packet.id, kind)),
                    packet.config.get(f"calendar_owner:{kind}") or str(packet.owner_id),
                )
                await db.commit()
                # The helper raises unless both providers confirm removal. An
                # unreachable or unconnected calendar keeps the flag set for the
                # next pass instead of stopping this packet's other follow-ups.
                try:
                    await remove_task_from_calendars_now(
                        task_id, str(tenant_id), owner_id
                    )
                    removed = True
                except Exception:
                    logger.warning(
                        "Calendar cleanup for intake task %s is still pending",
                        task_id,
                    )
                    removed = False
                await set_tenant_context(db, str(tenant_id))
                packet = await get_packet(db, tenant_id, matter_id, lock=True)
                if removed:
                    packet.config = {**packet.config, cleanup: False}
                    await db.commit()
            flag = f"staff_notified:{kind}"
            if packet.config.get(flag):
                continue
            task = await db.scalar(
                select(Task).where(
                    Task.id == uuid.uuid5(packet.id, kind), Task.tenant_id == tenant_id
                )
            )
            if task and task.status not in ("completed", "cancelled"):
                packet.config = {**packet.config, flag: True}
                await db.commit()
                await set_tenant_context(db, str(tenant_id))
                await notify_task_created(db, task, str(tenant_id))


async def tick():
    async with async_session_maker() as db:
        tenants = list(
            (
                await db.scalars(
                    select(Tenant.id).where(
                        Tenant.is_active.is_(True), Tenant.billing_tier != "demo"
                    )
                )
            ).all()
        )
    for tenant_id in tenants:
        async with async_session_maker() as db:
            await set_tenant_context(db, str(tenant_id))
            matters = list(
                (
                    await db.scalars(
                        select(MatterIntake.matter_id).where(
                            MatterIntake.tenant_id == tenant_id
                        )
                    )
                ).all()
            )
        for matter_id in matters:
            try:
                await process_packet(tenant_id, matter_id)
            except Exception:
                logger.exception(
                    "Intake reconciliation failed for matter %s", matter_id
                )
