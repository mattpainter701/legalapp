"""Durable intake: two requirements, explicit delivery claims and timed staff work."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
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
from app.services.email import email_service
from app.services.matter_access import can_access_matter
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
        "signature_id": str(packet.signature_id),
    }
    if not client:
        data.update(delivery=packet.delivery, owner_id=str(packet.owner_id))
    else:
        data["requirements"] = {
            name: {
                key: value
                for key, value in requirement.items()
                if key in ("completed", "completed_at", "sent_at")
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


def queue(packet, kind):
    delivery = dict(packet.delivery)
    for channel in packet.config["channels"]:
        key = f"{kind}:{channel}"
        delivery.setdefault(key, {"state": "queued", "attempt": 0})
    packet.delivery = delivery


def event(db, packet, title, content):
    db.add(
        MatterEvent(
            tenant_id=packet.tenant_id,
            matter_id=packet.matter_id,
            event_type="intake",
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
            description=f"Intake action due {due.isoformat()}. Client meeting options: conference call or in-person.",
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
    if not content.startswith(b"%PDF-") or len(content) > 20 * 1024 * 1024:
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
                disclosure_version="intake-notifications-v1",
                consent_timezone=body.timezone,
                quiet_hours_start="20:00",
                quiet_hours_end="08:00",
                allowed_categories=["intake"],
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
    stored = await store_file(
        user.tenant_id, matter, f"intake-{packet_id}.pdf", content, "application/pdf"
    )
    if not stored.succeeded:
        raise HTTPException(
            503, "Agreement storage is unavailable. Reconnect storage and retry."
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
        portal_visible=True,
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
        signature_id=signature.id,
        invite_id=invite.id,
        encrypted_invite=encrypt_token(token),
        status="awaiting_documents",
        config={
            **body.model_dump(mode="json"),
            "request_hash": request_hash,
            "source_sha256": signature.source_document_sha256,
        },
        requirements={
            "fee_agreement": {"completed": False},
            "questionnaire": {"completed": False},
        },
        answers={},
        delivery={},
    )
    queue(packet, "welcome")
    db.add(packet)
    matter.portal_enabled = True
    await set_intake_stage(db, matter, "Intake / Awaiting Documents")
    event(
        db,
        packet,
        "Intake started",
        "Fee agreement and questionnaire requested; portal delivery queued.",
    )
    await db.commit()
    return packet


async def cancel_packet(db, packet, reason):
    packet.status = "cancelled"
    for kind in ("documents", "scheduling", "delivery"):
        await close_task(db, packet, kind, reason)
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
    packet.delivery = {
        key: {**state, "state": "cancelled"}
        if state["state"] in ("queued", "blocked", "failed")
        else state
        for key, state in packet.delivery.items()
    }


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
        and signature.source_document_sha256 == packet.config["source_sha256"]
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
        if artifact and not packet.requirements["fee_agreement"]["completed"]:
            packet.requirements = {
                **packet.requirements,
                "fee_agreement": {
                    "completed": True,
                    "completed_at": signature.completed_at.isoformat(),
                    "evidence": "signature_acknowledgment_certificate",
                    "signature_id": str(signature.id),
                },
            }
    complete = all(
        packet.requirements[k]["completed"] for k in ("fee_agreement", "questionnaire")
    )
    if complete and packet.completed_at is None:
        times = [
            datetime.fromisoformat(packet.requirements[k]["completed_at"])
            for k in ("fee_agreement", "questionnaire")
        ]
        packet.completed_at = max(times)
        packet.status = "documents_complete"
        await set_intake_stage(db, matter, "Intake / Schedule Initial Meeting")
        await close_task(db, packet, "documents", "Both intake requirements completed")
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
            "Fee agreement and questionnaire complete. Schedule the initial meeting within 24 hours.",
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


def message(packet, kind, url):
    if kind == "welcome":
        return (
            "Welcome — complete your intake",
            f"Please review and sign your fee agreement and complete your questionnaire in your secure client portal: {url}",
        )
    if kind == "reminder":
        missing = [
            label
            for key, label in (
                ("fee_agreement", "fee agreement"),
                ("questionnaire", "questionnaire"),
            )
            if not packet.requirements[key]["completed"]
        ]
        return (
            "Your intake needs attention",
            f"Please complete your {' and '.join(missing)} in your secure portal: {url}",
        )
    if kind == "meeting":
        return (
            "Initial meeting confirmed",
            f"Your initial meeting details are available in your secure portal: {url}",
        )
    return (
        "Your intake documents are complete",
        f"Thank you. Your legal team will contact you to arrange a conference call or in-person meeting. Your secure portal: {url}",
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
    url = f"{get_settings().FRONTEND_URL.rstrip('/')}/portal/client/accept?token={decrypt_token(packet.encrypted_invite)}"
    subject, body = message(packet, kind, url)
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
                subject=subject,
                html_body=f"<p>{escape(body)}</p>",
                text_body=body,
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
                body=body,
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
                    subject=subject,
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

        for kind in ("documents", "scheduling", "delivery"):
            await set_tenant_context(db, str(tenant_id))
            packet = await get_packet(db, tenant_id, matter_id, lock=True)
            cleanup = f"calendar_cleanup:{kind}"
            if packet.config.get(cleanup):
                task_id, owner_id = (
                    str(uuid.uuid5(packet.id, kind)),
                    packet.config.get(f"calendar_owner:{kind}") or str(packet.owner_id),
                )
                await db.commit()
                results = await remove_task_from_calendars_now(
                    task_id, str(tenant_id), owner_id
                )
                await set_tenant_context(db, str(tenant_id))
                packet = await get_packet(db, tenant_id, matter_id, lock=True)
                if not any(isinstance(result, Exception) for result in results):
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
