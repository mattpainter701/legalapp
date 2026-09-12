"""In-document signing end to end: create, fill and sign, upload, review, retry."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.matter_document import MatterDocument
from app.models.plugin import MatterEvent
from app.models.signature import SignatureRequest, SignatureSigner
from app.routers import esignature as esignature_router
from app.services import matter_file_store as matter_store_module
from app.services.esign import service as esign_service
from app.services.esign.service import retry_pending_completions
from app.services.matter_file_store import MatterFileStore, StorageResult
from app.services.pdf_templates import _widgets
from app.models.client_portal import ClientPortalInvite
from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter
from app.routers.client_portal import CLIENT_PORTAL_COOKIE_NAME
from app.services.portal_token import create_matter_portal_token
from tests.esign_pdf_fixtures import acroform_pdf, flat_agreement_pdf

PORTAL = "/api/portal/client"
CLIENT_EMAIL = "client@example.com"


@pytest_asyncio.fixture(autouse=True)
async def _fresh_portal_state(test_redis):
    """Portal rate-limit counters live in Redis; isolate tests."""
    await test_redis.flushdb()
    yield
    await test_redis.flushdb()


@pytest_asyncio.fixture
async def portal_matter(db_session, test_tenant, test_user):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"signing-matter-{uuid.uuid4().hex[:8]}",
        matter_name="Rivera v. Northline Freight",
        matter_type="litigation",
        status="open",
        portal_enabled=True,
    )
    db_session.add(matter)
    db_session.add(
        MatterAssignment(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            matter_id=matter.id,
            user_id=test_user.id,
            role="lead",
            is_primary=True,
        )
    )
    await db_session.commit()
    await db_session.refresh(matter)
    return matter


@pytest_asyncio.fixture
async def portal_cookie(db_session, test_tenant, portal_matter):
    """A minted portal session cookie for the client of the fixture matter."""
    raw_token = secrets.token_urlsafe(32)
    invite = ClientPortalInvite(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        matter_id=portal_matter.id,
        contact_id=None,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        email=CLIENT_EMAIL,
        expires_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    db_session.add(invite)
    await db_session.commit()
    return create_matter_portal_token(
        tenant_id=str(test_tenant.id),
        matter_id=str(portal_matter.id),
        contact_id=None,
        email=CLIENT_EMAIL,
        invite_id=str(invite.id),
    )


def _portal_headers(token: str) -> dict:
    # The portal cookie is the real transport; the Authorization header the
    # shared ``client`` fixture sets is a firm token and must not be sent here.
    return {"Cookie": f"{CLIENT_PORTAL_COOKIE_NAME}={token}", "Authorization": ""}


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    """Store and read matter files on disk under a scratch upload root."""
    monkeypatch.setattr(matter_store_module.settings, "UPLOAD_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def captured_uploads(monkeypatch):
    """Route every 'signed' filing through a fake store and keep the bytes."""
    uploads: list[dict] = []

    async def store(**kwargs):
        uploads.append(kwargs)
        return StorageResult(
            provider="local",
            backend="local",
            storage_path=f"fixture/{kwargs['filename']}",
            provider_item_id=f"item-{len(uploads)}",
        )

    monkeypatch.setattr(esign_service._file_store, "store_matter_file_result", store)
    monkeypatch.setattr(
        esignature_router.matter_file_store, "store_matter_file_result", store
    )
    return uploads


async def _stored_document(db_session, tenant, matter, *, content, filename):
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


async def _sent_request(
    db_session, tenant, user, matter, document, content, *, role="client"
):
    request = SignatureRequest(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        matter_id=matter.id,
        document_id=document.id,
        status="sent",
        provider="internal",
        created_by_user_id=user.id,
        source_document_sha256=hashlib.sha256(content).hexdigest(),
        source_document_size=len(content),
        source_document_filename=document.filename,
        sent_at=datetime.now(timezone.utc),
    )
    request.signers = [
        SignatureSigner(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            request_id=request.id,
            name="Jane Client",
            email=CLIENT_EMAIL,
            role=role,
            sign_order=0,
            status="pending",
        )
    ]
    db_session.add(request)
    await db_session.commit()
    return request


async def _reload(db_session, request_id):
    # Refresh from the row the API committed without expiring every other
    # fixture object (an expired object would lazy-load synchronously).
    return await db_session.scalar(
        select(SignatureRequest)
        .options(selectinload(SignatureRequest.signers))
        .where(SignatureRequest.id == request_id)
        .execution_options(populate_existing=True)
    )


@pytest.mark.asyncio
async def test_create_plans_placements_and_exposes_the_staff_manifest(
    client, db_session, test_tenant, test_user, portal_matter, local_storage
):
    source = flat_agreement_pdf()
    document = await _stored_document(
        db_session, test_tenant, portal_matter, content=source, filename="Agreement.pdf"
    )
    body = {
        "document_id": str(document.id),
        "signers": [{"name": "Jane Client", "email": CLIENT_EMAIL, "role": "client"}],
    }

    external = await client.post(
        f"/api/matters/{portal_matter.id}/signatures",
        json={**body, "provider": "dropbox_sign"},
    )
    assert external.status_code == 422
    assert external.json()["detail"] == esignature_router.ONLY_INTERNAL_PROVIDER

    created = await client.post(
        f"/api/matters/{portal_matter.id}/signatures", json=body
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["provider"] == "internal"
    assert payload["fill_supported"] is True
    assert payload["signature_fields_count"] == 2
    assert payload["placement_source"] == "detected"
    assert payload["completion_pending"] is False
    assert [f["field_id"] for f in payload["positioned_fields"]] == [
        "auto:sig:1",
        "auto:date:1",
    ]
    assert all(f["source"] == "detected" for f in payload["positioned_fields"])
    assert all(
        f["source_sha256"] == payload["source_document_sha256"]
        for f in payload["positioned_fields"]
    )

    fields = await client.get(
        f"/api/matters/{portal_matter.id}/signatures/{payload['id']}/fields"
    )
    assert fields.status_code == 200, fields.text
    staff = fields.json()
    assert staff["pages"] == [{"page": 1, "width": 612.0, "height": 792.0}]
    assert staff["signer_id"] is None
    assert [f["kind"] for f in staff["fields"]] == ["signature", "date"]
    assert all("mine" not in f for f in staff["fields"])
    assert staff["fields"][0]["detected"] is True


@pytest.mark.asyncio
async def test_portal_fills_the_form_signs_and_files_the_executed_copy(
    client,
    db_session,
    test_tenant,
    test_user,
    portal_matter,
    portal_cookie,
    local_storage,
    captured_uploads,
):
    source = acroform_pdf()
    document = await _stored_document(
        db_session,
        test_tenant,
        portal_matter,
        content=source,
        filename="Intake form.pdf",
    )
    request = await _sent_request(
        db_session, test_tenant, test_user, portal_matter, document, source
    )
    headers = _portal_headers(portal_cookie)

    fields = await client.get(
        f"{PORTAL}/signatures/{request.id}/fields", headers=headers
    )
    assert fields.status_code == 200, fields.text
    manifest = fields.json()
    assert manifest["fill_supported"] is True
    assert manifest["signer_role"] == "client"
    by_id = {f["field_id"]: f for f in manifest["fields"]}
    assert by_id["acroform:client_name"]["mine"] is True
    assert by_id["acroform:client_name"]["value"] == ""
    assert by_id["acroform:client_signature"]["kind"] == "signature"
    assert by_id["acroform:client_signature"]["mine"] is True

    url = f"{PORTAL}/signatures/{request.id}/sign"
    consent = {
        "typed_signature": "Jane Q. Client",
        "consent_to_electronic_signature": True,
    }
    incomplete = await client.post(
        url, headers=headers, json={**consent, "field_values": {}}
    )
    assert incomplete.status_code == 422, incomplete.text
    assert incomplete.json()["detail"]["problems"] == [
        "Client name is required",
        "agree must be checked",
        "plan is required",
    ]
    unknown = await client.post(
        url,
        headers=headers,
        json={
            **consent,
            "field_values": {
                "acroform:client_name": "J",
                "acroform:agree": "true",
                "nope": "1",
            },
        },
    )
    assert unknown.status_code == 422
    assert any("Unknown field" in p for p in unknown.json()["detail"]["problems"])

    signed = await client.post(
        url,
        headers=headers,
        json={
            **consent,
            "field_values": {
                "acroform:client_name": "Jane Q. Client",
                "acroform:agree": "true",
                "acroform:state": "OK",
                "acroform:plan": "B",
            },
        },
    )
    assert signed.status_code == 200, signed.text
    body = signed.json()
    assert body["status"] == "completed"
    assert body["completion_pending"] is False
    assert body["executed_document_id"]
    assert body["signers"][0]["method"] == "portal_inline"
    assert "completion_error" in body and body["completion_error"] is None

    durable = await _reload(db_session, request.id)
    assert durable.signers[0].field_values["acroform:state"] == "OK"
    assert durable.signers[0].audit["field_count"] == 4
    executed = await db_session.get(MatterDocument, durable.executed_document_id)
    assert executed.document_category == "signed" and executed.portal_visible
    assert executed.filename.startswith("Intake-form-signed-")
    assert executed.description.startswith("Executed copy")
    certificate = await db_session.get(
        MatterDocument, uuid.UUID(durable.provider_envelope_id)
    )
    assert certificate is not None and certificate.id != executed.id
    filenames = [upload["filename"] for upload in captured_uploads]
    assert filenames[0] == executed.filename and filenames[1] == certificate.filename
    executed_bytes = captured_uploads[0]["content"]
    reader = PdfReader(BytesIO(executed_bytes))
    assert _widgets(reader) == []
    text = reader.pages[0].extract_text()
    assert "Jane Q. Client" in text and "Signed electronically" in text
    assert executed.document_sha256 == hashlib.sha256(executed_bytes).hexdigest()
    event = await db_session.scalar(
        select(MatterEvent).where(
            MatterEvent.matter_id == portal_matter.id,
            MatterEvent.title.like("Signing completed%"),
        )
    )
    assert event is not None and executed.filename in event.content


@pytest.mark.asyncio
async def test_uploaded_signed_copy_waits_for_staff_and_accept_completes(
    client,
    db_session,
    test_tenant,
    test_user,
    portal_matter,
    portal_cookie,
    local_storage,
    captured_uploads,
):
    source = flat_agreement_pdf()
    document = await _stored_document(
        db_session, test_tenant, portal_matter, content=source, filename="Agreement.pdf"
    )
    request = await _sent_request(
        db_session, test_tenant, test_user, portal_matter, document, source
    )
    headers = _portal_headers(portal_cookie)
    url = f"{PORTAL}/signatures/{request.id}/upload"

    not_pdf = await client.post(
        url, headers=headers, files={"file": ("scan.txt", b"hello", "text/plain")}
    )
    assert not_pdf.status_code == 422

    uploaded = await client.post(
        url,
        headers=headers,
        files={"file": ("scan.pdf", source, "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["status"] == "partially_signed"
    assert body["submitted_document_id"] and body["submitted_at"]
    assert body["completion_pending"] is False
    assert body["executed_document_id"] is None
    assert body["signers"][0]["status"] == "signed"
    assert body["signers"][0]["method"] == "uploaded_copy"
    submitted = await db_session.get(
        MatterDocument, uuid.UUID(body["submitted_document_id"])
    )
    assert submitted.document_category == "client_uploads"
    assert submitted.uploaded_by_user_id is None and submitted.portal_visible
    assert submitted.filename == "Agreement-signed-by-client.pdf"

    # The client still sees the request while it awaits review.
    listed = await client.get(f"{PORTAL}/signatures", headers=headers)
    assert [item["id"] for item in listed.json()] == [str(request.id)]

    # Nothing can complete behind staff's back.
    assert (
        await retry_pending_completions(
            db_session, now=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        == 0
    )

    accepted = await client.post(
        f"/api/matters/{portal_matter.id}/signatures/{request.id}/accept-submission"
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "completed"
    assert accepted.json()["executed_document_id"] == body["submitted_document_id"]
    durable = await _reload(db_session, request.id)
    assert durable.signers[0].audit["accepted_by_user_id"] == str(test_user.id)
    assert durable.signers[0].audit["accepted_at"]
    # Only the certificate was filed on completion: the upload itself was
    # already stored on the matter when the client sent it.
    filed = [u for u in captured_uploads if u["category"] == "signed"]
    assert len(filed) == 1
    assert "signature-evidence" in filed[0]["filename"]

    again = await client.post(
        f"/api/matters/{portal_matter.id}/signatures/{request.id}/accept-submission"
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_rejecting_an_upload_reopens_the_signer_and_asks_them_to_sign_again(
    client,
    db_session,
    test_tenant,
    test_user,
    portal_matter,
    portal_cookie,
    local_storage,
    captured_uploads,
    monkeypatch,
):
    source = flat_agreement_pdf()
    document = await _stored_document(
        db_session, test_tenant, portal_matter, content=source, filename="Agreement.pdf"
    )
    request = await _sent_request(
        db_session, test_tenant, test_user, portal_matter, document, source
    )
    headers = _portal_headers(portal_cookie)
    uploaded = await client.post(
        f"{PORTAL}/signatures/{request.id}/upload",
        headers=headers,
        files={"file": ("scan.pdf", source, "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    notified = AsyncMock()
    monkeypatch.setattr(esignature_router, "notify_signer", notified)

    empty_reason = await client.post(
        f"/api/matters/{portal_matter.id}/signatures/{request.id}/reject-submission",
        json={"reason": ""},
    )
    assert empty_reason.status_code == 422

    rejected = await client.post(
        f"/api/matters/{portal_matter.id}/signatures/{request.id}/reject-submission",
        json={"reason": "Page two is missing"},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "sent"
    assert body["submitted_document_id"] is None and body["submitted_at"] is None
    assert body["signers"][0]["status"] == "pending"
    assert body["signers"][0]["method"] is None
    durable = await _reload(db_session, request.id)
    signer = durable.signers[0]
    assert signer.signed_at is None
    assert signer.audit["rejections"][0]["reason"] == "Page two is missing"
    assert signer.audit["rejections"][0]["rejected_by_user_id"] == str(test_user.id)
    assert notified.await_count == 1
    assert notified.await_args.kwargs["kind"] == "resubmit"
    event = await db_session.scalar(
        select(MatterEvent).where(
            MatterEvent.matter_id == portal_matter.id,
            MatterEvent.title.like("Uploaded signed copy returned%"),
        )
    )
    assert event is not None and "Page two is missing" in event.content

    nothing_pending = await client.post(
        f"/api/matters/{portal_matter.id}/signatures/{request.id}/reject-submission",
        json={"reason": "again"},
    )
    assert nothing_pending.status_code == 409


@pytest.mark.asyncio
async def test_pending_completion_is_retried_once_storage_returns(
    db_session, test_tenant, test_user, portal_matter, local_storage, monkeypatch
):
    source = flat_agreement_pdf()
    document = await _stored_document(
        db_session, test_tenant, portal_matter, content=source, filename="Agreement.pdf"
    )
    request = await _sent_request(
        db_session, test_tenant, test_user, portal_matter, document, source
    )
    signer = request.signers[0]
    signer.status = "signed"
    signer.signed_at = datetime.now(timezone.utc)
    signer.typed_signature = "Jane Client"
    signer.method = "portal_inline"
    signer.audit = {"method": "portal_inline"}
    request.status = "partially_signed"
    await db_session.commit()
    request_id = request.id

    outage = AsyncMock(
        return_value=StorageResult(
            provider="microsoft",
            backend="onedrive",
            error="Configured Microsoft OneDrive storage is unavailable",
        )
    )
    monkeypatch.setattr(esign_service._file_store, "store_matter_file_result", outage)
    assert await retry_pending_completions(db_session) == 0
    durable = await _reload(db_session, request_id)
    assert durable.status == "partially_signed"
    assert "OneDrive" in durable.completion_error
    first_attempt = durable.completion_attempted_at
    assert first_attempt is not None
    failure_events = (
        (
            await db_session.execute(
                select(MatterEvent).where(
                    MatterEvent.matter_id == portal_matter.id,
                    MatterEvent.title == esign_service.STORAGE_FAILURE_EVENT_TITLE,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(failure_events) == 1

    # Within the retry window nothing is attempted, even if storage is back.
    uploads: list[dict] = []

    async def store(**kwargs):
        uploads.append(kwargs)
        return StorageResult(
            provider="local",
            backend="local",
            storage_path=f"fixture/{kwargs['filename']}",
        )

    monkeypatch.setattr(esign_service._file_store, "store_matter_file_result", store)
    assert await retry_pending_completions(db_session) == 0
    assert uploads == []

    later = datetime.now(timezone.utc) + esign_service.COMPLETION_RETRY_INTERVAL
    assert await retry_pending_completions(db_session, now=later) == 1
    durable = await _reload(db_session, request_id)
    assert durable.status == "completed"
    assert durable.completion_error is None
    assert durable.executed_document_id is not None
    assert durable.provider_envelope_id
    assert [upload["category"] for upload in uploads] == ["signed", "signed"]
    # Completed requests fall out of the retry query.
    assert await retry_pending_completions(db_session, now=later) == 0
