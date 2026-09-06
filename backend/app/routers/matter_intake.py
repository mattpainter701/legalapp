"""Staff intake controls and matter-authorized client questionnaire submission."""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.routers.client_portal import portal_matter_dep
from app.schemas.matter_intake import (
    IntakeAnswers,
    IntakeMeeting,
    IntakeReceipt,
    IntakeRetry,
    IntakeStart,
)
from app.services import matter_intake as service
from app.services.access_control import require_capability
from app.services.matter_access import can_access_matter

router = APIRouter(prefix="/api/matters", tags=["matter-intake"])
portal_router = APIRouter(prefix="/api/portal/client/intake", tags=["client-intake"])


async def staff_matter(db, user, matter_id):
    await set_tenant_context(db, str(user.tenant_id))
    if not await can_access_matter(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        is_admin=user.role == "admin",
        matter_id=matter_id,
    ):
        raise HTTPException(404, "Matter not found")
    return await db.scalar(
        select(Matter)
        .where(Matter.id == matter_id, Matter.tenant_id == user.tenant_id)
        .with_for_update(of=Matter)
    )


async def staff_packet(db, user, matter_id):
    await staff_matter(db, user, matter_id)
    packet = await service.get_packet(db, user.tenant_id, matter_id, lock=True)
    if packet is None:
        raise HTTPException(404, "Intake not started")
    return packet


@router.post("/{matter_id}/intake")
async def start(
    matter_id: uuid.UUID,
    options: str = Form(...),
    agreement: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    try:
        body = IntakeStart.model_validate_json(options)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            422, "Check the email, questions, delivery channels and timezone."
        ) from exc
    matter = await staff_matter(db, user, matter_id)
    packet = await service.start_packet(
        db,
        user,
        matter,
        body,
        agreement.filename or "Fee agreement.pdf",
        await agreement.read(20 * 1024 * 1024 + 1),
    )
    return service.public_packet(packet)


@router.get("/{matter_id}/intake")
async def read(
    matter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    packet = await staff_packet(db, user, matter_id)
    await service.reconcile(db, packet)
    await db.commit()
    return service.public_packet(packet)


@router.post("/{matter_id}/intake/receipt")
async def receipt(
    matter_id: uuid.UUID,
    body: IntakeReceipt,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    packet = await staff_packet(db, user, matter_id)
    if packet.status in ("cancelled", "scheduled"):
        raise HTTPException(409, "This intake is no longer accepting documents.")
    doc = await db.scalar(
        select(MatterDocument).where(
            MatterDocument.id == body.document_id,
            MatterDocument.tenant_id == user.tenant_id,
            MatterDocument.matter_id == matter_id,
        )
    )
    if doc is None:
        raise HTTPException(404, "Document not found")
    if not packet.requirements[body.requirement]["completed"]:
        packet.requirements = {
            **packet.requirements,
            body.requirement: {
                "completed": True,
                "completed_at": service.now().isoformat(),
                "document_id": str(doc.id),
                "verified_by": str(user.id),
                "evidence": "staff_verified",
                "note": body.note,
            },
        }
        service.event(
            db,
            packet,
            "Intake document receipt verified",
            f"{body.requirement}: document {doc.id}; verified by {user.id}.",
        )
    if body.requirement == "fee_agreement":
        signature = await db.get(service.SignatureRequest, packet.signature_id)
        if signature and signature.status not in ("completed", "voided"):
            signature.status = "voided"
            signature.voided_at = service.now()
            signature.void_reason = "Signed agreement received and verified by staff"
    await service.reconcile(db, packet)
    await db.commit()
    return service.public_packet(packet)


@router.post("/{matter_id}/intake/meeting")
async def meeting(
    matter_id: uuid.UUID,
    body: IntakeMeeting,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    packet = await staff_packet(db, user, matter_id)
    await service.reconcile(db, packet)
    if packet.status not in ("documents_complete", "scheduled"):
        raise HTTPException(
            409, "Complete both intake requirements before booking the initial meeting."
        )
    payload = body.model_dump(mode="json")
    if packet.meeting and packet.meeting != payload:
        raise HTTPException(
            409,
            "This intake meeting is already recorded; coordinate changes with the client.",
        )
    if packet.meeting is None:
        packet.meeting = payload
        packet.status = "scheduled"
        await service.close_task(
            db, packet, "scheduling", "Initial client meeting scheduled"
        )
        matter = await db.get(Matter, matter_id)
        matter.stage = "Intake / Initial Meeting Scheduled"
        service.queue(packet, "meeting")
        service.event(
            db,
            packet,
            "Initial meeting scheduled",
            f"{body.kind} at {body.starts_at.isoformat()}",
        )
    await db.commit()
    return service.public_packet(packet)


@router.post("/{matter_id}/intake/retry")
async def retry(
    matter_id: uuid.UUID,
    body: IntakeRetry,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    packet = await staff_packet(db, user, matter_id)
    state = packet.delivery.get(body.delivery_key)
    if (
        not state
        or state["state"] not in ("failed", "blocked", "unknown")
        or packet.status == "cancelled"
    ):
        raise HTTPException(409, "This delivery cannot be retried.")
    packet.delivery = {
        **packet.delivery,
        body.delivery_key: {"state": "queued", "attempt": state["attempt"] + 1},
    }
    service.event(
        db,
        packet,
        "Intake delivery retry authorized",
        f"{body.delivery_key}; {user.id} confirmed the prior message was not sent.",
    )
    await db.commit()
    return service.public_packet(packet)


@router.post("/{matter_id}/intake/renew-invitation")
async def renew_invitation(
    matter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    packet = await staff_packet(db, user, matter_id)
    await service.reconcile(db, packet)
    if packet.status == "cancelled":
        raise HTTPException(409, "This intake is closed.")
    if any(state["state"] == "sending" for state in packet.delivery.values()):
        raise HTTPException(
            409, "A notification is being sent. Check delivery before renewing."
        )
    contact = await db.get(service.Contact, packet.contact_id)
    if not contact or (contact.email or "").lower() != packet.config["email"].lower():
        raise HTTPException(
            409, "The client email changed; review portal access with an administrator."
        )
    prior = await db.get(service.ClientPortalInvite, packet.invite_id)
    prior.revoked = True
    token = service.secrets.token_urlsafe(32)
    invitation = service.ClientPortalInvite(
        id=uuid.uuid4(),
        tenant_id=packet.tenant_id,
        matter_id=packet.matter_id,
        contact_id=packet.contact_id,
        email=packet.config["email"],
        token_hash=service.hashlib.sha256(token.encode()).hexdigest(),
        expires_at=service.now() + service.timedelta(days=30),
        created_by_user_id=user.id,
    )
    db.add(invitation)
    await db.flush()
    packet.invite_id = invitation.id
    packet.encrypted_invite = service.encrypt_token(token)
    signature = await db.get(service.SignatureRequest, packet.signature_id)
    if signature and signature.status == "sent":
        signature.expires_at = invitation.expires_at
    kind = (
        "meeting"
        if packet.meeting
        else "complete"
        if packet.completed_at
        else "welcome"
    )
    delivery = dict(packet.delivery)
    for channel in packet.config["channels"]:
        key = f"{kind}:{channel}"
        delivery[key] = {
            "state": "queued",
            "attempt": delivery.get(key, {}).get("attempt", -1) + 1,
        }
    packet.delivery = delivery
    service.event(
        db,
        packet,
        "Portal invitation renewed",
        "Prior invitation revoked; replacement delivery queued by staff.",
    )
    await db.commit()
    return service.public_packet(packet)


@router.post("/{matter_id}/intake/cancel")
async def cancel(
    matter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    packet = await staff_packet(db, user, matter_id)
    await service.cancel_packet(db, packet, "Intake cancelled")
    await db.commit()
    return service.public_packet(packet)


async def client_packet(db, resolved):
    ctx, matter = resolved
    await set_tenant_context(db, ctx.tenant_id)
    packet = await service.get_packet(
        db, uuid.UUID(ctx.tenant_id), matter.id, lock=True
    )
    if (
        packet is None
        or str(packet.contact_id) != str(ctx.contact_id)
        or packet.config["email"].lower() != (ctx.email or "").lower()
    ):
        raise HTTPException(404, "Intake not found")
    return packet


@portal_router.get("")
async def client_read(
    resolved=Depends(portal_matter_dep), db: AsyncSession = Depends(get_db)
):
    packet = await client_packet(db, resolved)
    await service.reconcile(db, packet)
    await db.commit()
    return service.public_packet(packet, client=True)


@portal_router.post("/questionnaire")
async def submit(
    body: IntakeAnswers,
    resolved=Depends(portal_matter_dep),
    db: AsyncSession = Depends(get_db),
):
    packet = await client_packet(db, resolved)
    if packet.status != "awaiting_documents":
        if packet.answers == body.answers:
            return service.public_packet(packet, client=True)
        raise HTTPException(
            409, "The questionnaire is closed; contact your legal team for corrections."
        )
    allowed = {q["key"] for q in packet.config["questions"]}
    if set(body.answers) - allowed or any(
        len(v) > 20000 for v in body.answers.values()
    ):
        raise HTTPException(422, "Questionnaire contains unknown or oversized answers.")
    for q in packet.config["questions"]:
        if q["required"] and not body.answers.get(q["key"], "").strip():
            raise HTTPException(422, f"Complete: {q['label']}")
    if packet.requirements["questionnaire"]["completed"]:
        if packet.answers != body.answers:
            raise HTTPException(
                409, "Already submitted; contact staff to correct your answers."
            )
    else:
        matter = resolved[1]
        text = "Client questionnaire\n\n" + "\n\n".join(
            f"{q['label']}\n{body.answers.get(q['key'], '')}"
            for q in packet.config["questions"]
        )
        content = text.encode("utf-8")
        stored = await service.store_file(
            packet.tenant_id,
            matter,
            f"questionnaire-{packet.id}.txt",
            content,
            "text/plain",
        )
        if not stored.succeeded:
            raise HTTPException(503, "Questionnaire could not be saved. Please retry.")
        document = MatterDocument(
            id=uuid.uuid5(packet.id, "questionnaire"),
            tenant_id=packet.tenant_id,
            matter_id=packet.matter_id,
            filename="Completed client questionnaire.txt",
            content_type="text/plain",
            file_size=len(content),
            document_category="intake",
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
        packet.answers = body.answers
        packet.requirements = {
            **packet.requirements,
            "questionnaire": {
                "completed": True,
                "completed_at": service.now().isoformat(),
                "evidence": "portal_submission",
                "contact_id": str(packet.contact_id),
                "document_id": str(document.id),
            },
        }
        service.event(
            db,
            packet,
            "Client questionnaire received",
            "Client submitted the intake questionnaire with all required answers.",
        )
    await service.reconcile(db, packet)
    await db.commit()
    return service.public_packet(packet, client=True)
