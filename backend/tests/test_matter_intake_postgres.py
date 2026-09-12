"""PostgreSQL intake persistence and concurrent receipt coverage."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.models.contact import Contact
from app.models.matter_document import MatterDocument
from app.models.matter_intake import MatterIntake
from app.models.plugin import Matter, MatterEvent
from app.models.task import Task
from app.routers import matter_intake as routes
from app.schemas.matter_intake import IntakeReceipt, IntakeStart
from app.services import matter_intake as service


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["onedrive", "google_drive"])
async def test_concurrent_receipt_creates_one_scheduling_task(
    db_session, test_user, test_engine, monkeypatch, provider
):
    user = SimpleNamespace(id=test_user.id, tenant_id=test_user.tenant_id, role="admin")
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        first_name="Jane",
        last_name="Smith",
        email="jane@example.com",
    )
    db_session.add(contact)
    await db_session.flush()
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        slug=f"intake-{uuid.uuid4().hex[:8]}",
        matter_name="Smith",
        client_contact_id=contact.id,
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    monkeypatch.setattr(
        service,
        "store_file",
        AsyncMock(
            return_value=SimpleNamespace(
                succeeded=True,
                storage_path="provider/path",
                provider=provider,
                backend=provider,
                provider_item_id="item",
                drive_id="drive",
                parent_id="parent",
            )
        ),
    )
    monkeypatch.setattr(
        service, "get_user_capabilities", AsyncMock(return_value={"manage_matters"})
    )
    monkeypatch.setattr(
        service,
        "send_client_email",
        AsyncMock(
            return_value=SimpleNamespace(
                delivery_certainty="confirmed_sent", provider=provider
            )
        ),
    )
    fixed = datetime(2026, 9, 6, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "now", lambda: fixed)
    options = IntakeStart(
        email=contact.email,
        channels=["email"],
        questions=[dict(key="summary", label="Summary")],
        confirm_send=True,
    )
    packet = await service.start_packet(
        db_session, user, matter, options, "fee.pdf", b"%PDF-reviewed"
    )
    assert (
        await service.start_packet(
            db_session, user, matter, options, "fee.pdf", b"%PDF-reviewed"
        )
    ).id == packet.id
    packet_id, matter_id = packet.id, matter.id
    await service.deliver(db_session, packet, "welcome:email")
    docs = [
        MatterDocument(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            matter_id=matter_id,
            filename=name,
            storage_path="provider/path",
            storage_provider=provider,
            storage_backend=provider,
        )
        for name in ("signed.pdf", "questionnaire.txt")
    ]
    db_session.add_all(docs)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def receive(kind, doc_id):
        async with factory() as session:
            return await routes.receipt(
                matter_id,
                IntakeReceipt(
                    requirement=kind,
                    document_id=doc_id,
                    note="Reviewed complete document",
                ),
                session,
                user,
            )

    await asyncio.gather(
        receive("fee_agreement", docs[0].id), receive("questionnaire", docs[1].id)
    )
    await receive("fee_agreement", docs[0].id)
    await db_session.refresh(packet)
    assert packet.status == "documents_complete" and packet.completed_at == fixed
    assert packet.sent_at == fixed
    followup = await db_session.get(Task, uuid.uuid5(packet_id, "documents"))
    scheduled = await db_session.get(Task, uuid.uuid5(packet_id, "scheduling"))
    assert followup.status == "cancelled"
    assert scheduled.due_date == (fixed + timedelta(days=1)).date()
    assert scheduled.due_time.hour == 9
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MatterEvent)
            .where(
                MatterEvent.matter_id == matter_id,
                MatterEvent.title == "Intake documents complete",
            )
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MatterIntake)
            .where(MatterIntake.matter_id == matter_id)
        )
        == 1
    )
    other = SimpleNamespace(id=uuid.uuid4(), tenant_id=user.tenant_id, role="user")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as denied:
        await routes.read(matter_id, db_session, other)
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_jane_doe_selected_packet_fee_milestone_and_client_upload(
    db_session, test_user, monkeypatch
):
    """LARP: matter -> selected forms -> fee signed -> portal -> client upload."""
    from app.models.signature import SignatureRequest, SignatureSigner
    from app.services import matter_mail_attachments
    from app.services.mail_attachment import MailAttachment
    from app.schemas.matter_intake import IntakeSubmission
    from app.routers.client_portal import ClientPortalContext
    import hashlib

    user = SimpleNamespace(id=test_user.id, tenant_id=test_user.tenant_id, role="admin")
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
    )
    db_session.add(contact)
    await db_session.flush()
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        slug=f"jane-doe-{uuid.uuid4().hex[:8]}",
        matter_name="Jane Doe divorce",
        client_contact_id=contact.id,
        status="open",
    )
    db_session.add(matter)
    await db_session.flush()
    docs = [
        MatterDocument(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            matter_id=matter.id,
            filename=name,
            content_type="application/pdf",
            file_size=13,
            storage_path="fixture",
        )
        for name in [
            "Attorney fee agreement.pdf",
            "General intake.pdf",
            "Client questionnaire.pdf",
        ]
    ]
    db_session.add_all(docs)
    await db_session.commit()
    content = b"%PDF-reviewed"
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(
        matter_mail_attachments,
        "reviewed_attachment",
        AsyncMock(
            return_value=(
                MailAttachment("form.pdf", content, "application/pdf"),
                digest,
            )
        ),
    )
    monkeypatch.setattr(
        service, "get_user_capabilities", AsyncMock(return_value={"manage_matters"})
    )
    fixed = datetime(
        2026, 11, 1, 5, 30, tzinfo=timezone.utc
    )  # DST transition: 24 elapsed hours.
    monkeypatch.setattr(service, "now", lambda: fixed)
    options = IntakeStart(
        email=contact.email,
        channels=["email"],
        questions=[dict(key="summary", label="Summary")],
        confirm_send=True,
        agreement_document_id=docs[0].id,
        selected_documents=[
            dict(
                document_id=docs[1].id, label="General intake", requires_signature=True
            ),
            dict(
                document_id=docs[2].id, label="Questionnaire", requires_signature=False
            ),
        ],
        upload_requirements=[
            dict(key="upload_certificate", label="Marriage certificate")
        ],
        include_questionnaire=False,
        portal_after_signing=True,
    )
    packet = await service.start_packet(
        db_session, user, matter, options, docs[0].filename, content
    )
    packet_id, matter_id = packet.id, matter.id
    assert (
        len(
            (
                await db_session.scalars(
                    select(SignatureRequest).where(
                        SignatureRequest.matter_id == matter_id
                    )
                )
            ).all()
        )
        == 2
    )
    assert (
        len(
            (
                await db_session.scalars(
                    select(SignatureSigner).where(
                        SignatureSigner.tenant_id == user.tenant_id
                    )
                )
            ).all()
        )
        == 2
    )
    assert packet.requirements["questionnaire"]["required"] is False
    assert set(packet.delivery) == {"welcome:email"}
    await service.reconcile(db_session, packet)
    assert "signed:email" not in packet.delivery
    # Provider completion evidence, with its persisted acknowledgment artifact.
    artifact = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter_id,
        filename="Acknowledgment.pdf",
    )
    db_session.add(artifact)
    await db_session.flush()
    signature = await db_session.get(SignatureRequest, packet.signature_id)
    signature.status, signature.completed_at = "completed", fixed
    signature.completion_artifact_sha256 = "b" * 64
    signature.provider_envelope_id = str(artifact.id)
    await db_session.commit()
    await service.reconcile(db_session, packet)
    await db_session.commit()
    assert packet.requirements["fee_agreement"]["completed"] is True
    assert (
        packet.status == "awaiting_documents"
    )  # other forms never delay portal welcome
    assert packet.delivery["signed:email"]["state"] == "queued"
    assert (
        packet.config["signing_followup_due_at"]
        == (fixed + timedelta(hours=24)).isoformat()
    )
    followup = await db_session.get(Task, uuid.uuid5(packet_id, "signed"))
    assert (
        followup.due_time.hour == 23 and followup.due_date.isoformat() == "2026-11-01"
    )
    await service.reconcile(db_session, packet)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.external_ref == f"intake:{packet_id}:signed")
        )
        == 1
    )
    upload = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter_id,
        filename="Marriage certificate.pdf",
        document_category="client_uploads",
    )
    db_session.add(upload)
    await db_session.commit()
    ctx = ClientPortalContext(
        tenant_id=str(user.tenant_id),
        matter_id=str(matter_id),
        contact_id=str(contact.id),
        email=contact.email,
        invite_id=str(packet.invite_id),
    )
    result = await routes.submit_requirement(
        "upload_certificate",
        IntakeSubmission(document_id=upload.id),
        (ctx, matter),
        db_session,
    )
    assert result["requirements"]["upload_certificate"]["submitted_document_id"] == str(
        upload.id
    )
    assert not result["requirements"]["upload_certificate"]["completed"]
    await routes.submit_requirement(
        "upload_certificate",
        IntakeSubmission(document_id=upload.id),
        (ctx, matter),
        db_session,
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MatterEvent)
            .where(
                MatterEvent.matter_id == matter_id,
                MatterEvent.title == "Client document submitted",
            )
        )
        == 1
    )
    await routes.receipt(
        matter_id,
        IntakeReceipt(
            requirement="upload_certificate",
            document_id=upload.id,
            note="Reviewed certificate",
        ),
        db_session,
        user,
    )
    assert packet.requirements["upload_certificate"]["completed"]
    # Each remaining form has independent evidence; the final receipt advances intake.
    for doc in docs[1:]:
        await routes.receipt(
            matter_id,
            IntakeReceipt(
                requirement=f"document_{doc.id.hex}",
                document_id=doc.id,
                note="Completed form verified by attorney",
            ),
            db_session,
            user,
        )
    assert packet.status == "documents_complete"
    assert await db_session.get(Task, uuid.uuid5(packet_id, "scheduling"))
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SignatureRequest)
            .where(
                SignatureRequest.matter_id == matter_id,
                SignatureRequest.status == "voided",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_declined_agreement_reconciles_timeline_task_and_requirement(
    db_session, test_user
):
    from app.models.client_portal import ClientPortalInvite
    from app.models.signature import SignatureRequest

    tenant_id = test_user.tenant_id
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
    )
    db_session.add(contact)
    await db_session.flush()
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=test_user.id,
        slug=f"declined-{uuid.uuid4().hex[:8]}",
        matter_name="Jane Doe divorce",
        client_contact_id=contact.id,
        status="open",
    )
    db_session.add(matter)
    await db_session.flush()
    request = SignatureRequest(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter.id,
        status="declined",
        provider="internal",
        source_document_filename="Fee agreement.pdf",
        created_by_user_id=test_user.id,
        declined_at=service.now(),
        decline_reason="Wants a different fee structure",
    )
    invite = ClientPortalInvite(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter.id,
        contact_id=contact.id,
        token_hash="a" * 64,
        email=contact.email,
        expires_at=service.now() + timedelta(days=14),
    )
    db_session.add_all([request, invite])
    await db_session.flush()
    packet = MatterIntake(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        matter_id=matter.id,
        contact_id=contact.id,
        owner_id=test_user.id,
        created_by=test_user.id,
        signature_id=request.id,
        invite_id=invite.id,
        encrypted_invite="encrypted",
        status="awaiting_documents",
        config={"timezone": "America/Chicago"},
        requirements={"fee_agreement": {"completed": False, "due_at": None}},
        answers={},
        delivery={},
    )
    db_session.add(packet)
    await db_session.commit()

    await service.reconcile(db_session, packet)
    await db_session.commit()

    requirement = packet.requirements["fee_agreement"]
    assert requirement["declined"] is True
    assert requirement["decline_reason"] == "Wants a different fee structure"
    events = (
        await db_session.scalars(
            select(MatterEvent).where(
                MatterEvent.matter_id == matter.id,
                MatterEvent.title == "Fee agreement declined",
            )
        )
    ).all()
    assert len(events) == 1
    assert events[0].event_type == "signature"
    task = await db_session.get(Task, uuid.uuid5(packet.id, "declined:fee_agreement"))
    assert task is not None and task.status == "pending"

    # A reconcile pass runs on every packet touch, so the branch must not
    # duplicate the timeline entry or the rework task.
    await service.reconcile(db_session, packet)
    await db_session.commit()
    events = (
        await db_session.scalars(
            select(MatterEvent).where(MatterEvent.title == "Fee agreement declined")
        )
    ).all()
    assert len(events) == 1
