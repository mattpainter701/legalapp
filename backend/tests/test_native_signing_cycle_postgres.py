"""The whole paperwork cycle, as the firm and the client actually run it.

Staff send a fee agreement, an intake form, a questionnaire and one requested
record. The client signs the fee agreement on its printed signature line while
the portal is still limited to paperwork, fills and signs the intake form on
its own AcroForm fields, signs the questionnaire on paper and uploads it, and
uploads the record. Staff accept the uploaded copy and verify the record. Every
signature files an executed copy the client can open, the intake packet
reconciles each requirement from those signatures, the chase tasks close, and
the packet completes with a scheduling task.

Storage is the real local store under a scratch upload root, so the executed
copies are written, re-read and served exactly as they would be for an
unbound tenant.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pypdf import PdfReader
from sqlalchemy import func, select

from app.models.contact import Contact
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter, MatterEvent
from app.models.signature import SignatureRequest
from app.models.task import Task
from app.routers import matter_intake as intake_routes
from app.routers.client_portal import CLIENT_PORTAL_COOKIE_NAME
from app.schemas.matter_intake import IntakeReceipt, IntakeStart
from app.services import matter_file_store as matter_store_module
from app.services import matter_intake as intake_service
from app.services.matter_file_store import MatterFileStore
from app.services.pdf_templates import _widgets
from app.services.portal_token import create_matter_portal_token
from tests.esign_pdf_fixtures import (
    acroform_pdf,
    flat_agreement_pdf,
    label_below_rule_pdf,
)

PORTAL = "/api/portal/client"
CLIENT_EMAIL = "jane@example.com"


@pytest_asyncio.fixture(autouse=True)
async def _fresh_portal_state(test_redis):
    await test_redis.flushdb()
    yield
    await test_redis.flushdb()


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    """Every filing lands on disk under a scratch root and is read back from it."""
    monkeypatch.setattr(matter_store_module.settings, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


def _portal_headers(token: str) -> dict:
    return {"Cookie": f"{CLIENT_PORTAL_COOKIE_NAME}={token}", "Authorization": ""}


async def _stored_pdf(db_session, tenant, matter, *, content, filename):
    stored = await MatterFileStore()._store_local(
        str(tenant.id), matter.slug, "drafts", f"{uuid.uuid4().hex}_{filename}", content
    )
    document = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        matter_id=matter.id,
        filename=filename,
        content_type="application/pdf",
        file_size=len(content),
        storage_path=stored.storage_path,
        storage_provider="local",
        storage_backend="local",
        document_category="drafts",
        document_sha256=hashlib.sha256(content).hexdigest(),
    )
    db_session.add(document)
    await db_session.commit()
    return document


async def _task(db_session, packet_id, kind):
    return await db_session.get(Task, uuid.uuid5(packet_id, kind))


async def _packet(db_session, packet_id):
    from app.models.matter_intake import MatterIntake

    return await db_session.scalar(
        select(MatterIntake)
        .where(MatterIntake.id == packet_id)
        .execution_options(populate_existing=True)
    )


async def _request(db_session, request_id):
    return await db_session.scalar(
        select(SignatureRequest)
        .where(SignatureRequest.id == request_id)
        .execution_options(populate_existing=True)
    )


@pytest.mark.asyncio
async def test_paperwork_cycle_from_send_to_documents_complete(
    client, db_session, test_tenant, test_user, local_storage, monkeypatch
):
    user = SimpleNamespace(id=test_user.id, tenant_id=test_user.tenant_id, role="admin")
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        first_name="Jane",
        last_name="Client",
        email=CLIENT_EMAIL,
    )
    db_session.add(contact)
    await db_session.flush()
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"cycle-{uuid.uuid4().hex[:8]}",
        matter_name="Jane Client engagement",
        matter_type="business",
        client_contact_id=contact.id,
        status="open",
        portal_enabled=True,
    )
    db_session.add(matter)
    await db_session.commit()

    intake_form = await _stored_pdf(
        db_session, test_tenant, matter, content=acroform_pdf(), filename="Intake.pdf"
    )
    questionnaire_bytes = flat_agreement_pdf()
    questionnaire = await _stored_pdf(
        db_session,
        test_tenant,
        matter,
        content=questionnaire_bytes,
        filename="Questionnaire.pdf",
    )
    # The firm's own starter forms print the label under the ruled line.
    fee_bytes = label_below_rule_pdf("Client signature")

    monkeypatch.setattr(
        intake_service,
        "get_user_capabilities",
        AsyncMock(return_value={"manage_matters"}),
    )
    monkeypatch.setattr(
        intake_service,
        "send_client_email",
        AsyncMock(
            return_value=SimpleNamespace(
                delivery_certainty="confirmed_sent", provider="microsoft"
            )
        ),
    )
    due = datetime.now(timezone.utc) + timedelta(days=5)
    options = IntakeStart(
        email=CLIENT_EMAIL,
        channels=["email"],
        confirm_send=True,
        agreement_due_at=due,
        selected_documents=[
            dict(
                document_id=intake_form.id,
                label="Client intake form",
                requires_signature=True,
                due_at=due,
            ),
            dict(
                document_id=questionnaire.id,
                label="Client questionnaire",
                requires_signature=True,
            ),
        ],
        upload_requirements=[dict(key="upload_1", label="Formation documents")],
        portal_after_signing=True,
    )
    # The drawer no longer asks for typed questions; the packet carries none.
    assert options.include_questionnaire is False and options.questions == []

    packet = await intake_service.start_packet(
        db_session, user, matter, options, "Fee agreement.pdf", fee_bytes
    )
    packet_id = packet.id
    await intake_service.deliver(db_session, packet, "welcome:email")
    await db_session.commit()
    packet = await _packet(db_session, packet_id)
    assert packet.sent_at is not None
    assert (await _task(db_session, packet_id, "documents")).status not in (
        "completed",
        "cancelled",
    )
    fee_request_id = packet.signature_id
    by_label = {
        item.get("label"): key
        for key, item in packet.requirements.items()
        if item.get("kind") == "signature"
    }
    intake_key, questionnaire_key = (
        by_label["Client intake form"],
        by_label["Client questionnaire"],
    )
    intake_request_id = uuid.UUID(packet.requirements[intake_key]["signature_id"])
    questionnaire_request_id = uuid.UUID(
        packet.requirements[questionnaire_key]["signature_id"]
    )
    # Every request planned somewhere to sign, without staff placing anything.
    fee_request = await _request(db_session, fee_request_id)
    assert fee_request.signing_plan["placement_source"] == "detected"
    assert fee_request.signing_plan["signature_fields_count"] == 2
    assert (await _request(db_session, intake_request_id)).signing_plan[
        "placement_source"
    ] == "acroform"

    headers = _portal_headers(
        create_matter_portal_token(
            tenant_id=str(test_tenant.id),
            matter_id=str(matter.id),
            contact_id=str(contact.id),
            email=CLIENT_EMAIL,
            invite_id=str(packet.invite_id),
        )
    )

    # ── Paperwork-only: the client can see the checklist and sign the fee
    # agreement, and nothing else, until it is signed.
    checklist = await client.get(f"{PORTAL}/intake", headers=headers)
    assert checklist.status_code == 200, checklist.text
    requirements = checklist.json()["requirements"]
    assert requirements["fee_agreement"]["completed"] is False
    assert requirements[intake_key]["kind"] == "signature"
    assert requirements["upload_1"]["kind"] == "upload"
    assert "questionnaire" not in {
        key for key, item in requirements.items() if item.get("required") is not False
    }
    assert (await client.get(f"{PORTAL}/messages", headers=headers)).status_code == 403

    fields = await client.get(
        f"{PORTAL}/signatures/{fee_request_id}/fields", headers=headers
    )
    assert fields.status_code == 200, fields.text
    manifest = fields.json()
    kinds = sorted(field["kind"] for field in manifest["fields"])
    assert kinds == ["date", "signature"]
    signature_field = next(f for f in manifest["fields"] if f["kind"] == "signature")
    assert signature_field["mine"] is True and signature_field["detected"] is True
    # The box sits on the ruled line the starter form printed at y=528.5.
    assert signature_field["rect"][1] < 528.5 < signature_field["rect"][3]

    signed = await client.post(
        f"{PORTAL}/signatures/{fee_request_id}/sign",
        headers=headers,
        json={
            "typed_signature": "Jane Client",
            "consent_to_electronic_signature": True,
            "field_values": {},
        },
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["status"] == "completed"
    assert signed.json()["completion_pending"] is False
    fee_executed_id = signed.json()["executed_document_id"]
    assert fee_executed_id

    packet = await _packet(db_session, packet_id)
    agreement = packet.requirements["fee_agreement"]
    assert agreement["completed"] is True
    assert agreement["evidence"] == "signature_acknowledgment_certificate"
    assert packet.status == "awaiting_documents"
    # Signing opened the portal and started the 24-hour follow-up clock; the
    # agreement's own due-date chase closed.
    assert (await _task(db_session, packet_id, "signed")).status not in (
        "completed",
        "cancelled",
    )
    assert (await _task(db_session, packet_id, "due:fee_agreement")).status == (
        "cancelled"
    )
    assert (await client.get(f"{PORTAL}/messages", headers=headers)).status_code == 200

    # The executed copy is filed, portal-visible, flat, and carries the name.
    download = await client.get(
        f"{PORTAL}/documents/{fee_executed_id}/download", headers=headers
    )
    assert download.status_code == 200, download.text
    assert download.content.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(download.content))
    assert _widgets(reader) == []
    assert "Jane Client" in reader.pages[0].extract_text()

    # ── Intake form: fill the AcroForm fields and sign in the document.
    fields = await client.get(
        f"{PORTAL}/signatures/{intake_request_id}/fields", headers=headers
    )
    assert fields.status_code == 200, fields.text
    by_id = {field["field_id"]: field for field in fields.json()["fields"]}
    assert by_id["acroform:client_signature"]["kind"] == "signature"
    signed = await client.post(
        f"{PORTAL}/signatures/{intake_request_id}/sign",
        headers=headers,
        json={
            "typed_signature": "Jane Client",
            "consent_to_electronic_signature": True,
            "field_values": {
                "acroform:client_name": "Jane Client",
                "acroform:agree": "true",
                "acroform:state": "TX",
                "acroform:plan": "A",
            },
        },
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["status"] == "completed"
    packet = await _packet(db_session, packet_id)
    assert packet.requirements[intake_key]["completed"] is True
    assert (await _task(db_session, packet_id, f"due:{intake_key}")).status == (
        "cancelled"
    )
    executed = await client.get(
        f"{PORTAL}/documents/{signed.json()['executed_document_id']}/download",
        headers=headers,
    )
    text = PdfReader(BytesIO(executed.content)).pages[0].extract_text()
    assert "Jane Client" in text and "Signed electronically" in text

    # ── Questionnaire: signed on paper, uploaded, reviewed by staff.
    uploaded = await client.post(
        f"{PORTAL}/signatures/{questionnaire_request_id}/upload",
        headers=headers,
        files={"file": ("signed.pdf", questionnaire_bytes, "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    submitted_id = uploaded.json()["submitted_document_id"]
    assert submitted_id and uploaded.json()["status"] != "completed"
    checklist = await client.get(f"{PORTAL}/intake", headers=headers)
    requirement = checklist.json()["requirements"][questionnaire_key]
    assert requirement["submitted_document_id"] == submitted_id
    assert requirement["completed"] is False
    packet = await _packet(db_session, packet_id)
    assert packet.status == "awaiting_documents"

    accepted = await client.post(
        f"/api/matters/{matter.id}/signatures/{questionnaire_request_id}/accept-submission"
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "completed"
    assert accepted.json()["executed_document_id"] == submitted_id
    packet = await _packet(db_session, packet_id)
    assert packet.requirements[questionnaire_key]["completed"] is True
    # The accepted copy stays on the requirement as the record of what staff
    # reviewed.
    assert packet.requirements[questionnaire_key]["submitted_document_id"] == (
        submitted_id
    )
    assert packet.status == "awaiting_documents"

    # ── The requested record: uploaded by the client, verified by staff.
    record = await client.post(
        f"{PORTAL}/documents/upload",
        headers=headers,
        files={"file": ("formation.pdf", b"%PDF-1.4 formation", "application/pdf")},
    )
    assert record.status_code == 201, record.text
    record_id = record.json()["id"]
    submission = await client.post(
        f"{PORTAL}/intake/requirements/upload_1/submission",
        headers=headers,
        json={"document_id": record_id},
    )
    assert submission.status_code == 200, submission.text
    assert submission.json()["requirements"]["upload_1"]["completed"] is False
    assert submission.json()["requirements"]["upload_1"]["submitted_document_id"] == (
        record_id
    )
    result = await intake_routes.receipt(
        matter.id,
        IntakeReceipt(
            requirement="upload_1",
            document_id=uuid.UUID(record_id),
            note="Formation documents reviewed and complete",
        ),
        db_session,
        user,
    )
    assert result["requirements"]["upload_1"]["completed"] is True
    assert result["requirements"]["upload_1"]["evidence"] == "staff_verified"

    # ── Everything is in: the packet completes and hands off to scheduling.
    packet = await _packet(db_session, packet_id)
    assert packet.status == "documents_complete"
    assert packet.completed_at is not None
    assert (await _task(db_session, packet_id, "documents")).status == "cancelled"
    scheduling = await _task(db_session, packet_id, "scheduling")
    assert scheduling is not None and scheduling.status not in (
        "completed",
        "cancelled",
    )
    assert packet.delivery["complete:email"]["state"] == "queued"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MatterEvent)
            .where(
                MatterEvent.matter_id == matter.id,
                MatterEvent.title == "Intake documents complete",
            )
        )
        == 1
    )
    # Three signature requests, all completed, each with a filed executed copy
    # and an evidence certificate the firm can open on the matter.
    requests = (
        await db_session.scalars(
            select(SignatureRequest)
            .where(SignatureRequest.matter_id == matter.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert len(requests) == 3
    assert {request.status for request in requests} == {"completed"}
    for request in requests:
        assert request.executed_document_id is not None
        certificate = await db_session.get(
            MatterDocument, uuid.UUID(request.provider_envelope_id)
        )
        assert certificate is not None and certificate.document_category == "signed"
