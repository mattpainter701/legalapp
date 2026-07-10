from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
import uuid

import pytest

from app.models.matter_document import MatterDocument
from app.models.signature import SignatureRequest, SignatureSigner
import app.routers.esignature as esignature_router
from app.routers.esignature import _source_document_is_unchanged
from app.schemas.signature import PortalSignRequest
from app.services.esign.service import (
    complete_request_if_done,
    mark_request_expired_if_needed,
    next_pending_signers,
    record_portal_decline,
    signer_can_act_now,
    record_portal_signature,
)
from app.services.matter_file_store import MatterFileIntegrityError, StorageResult
import app.services.esign.service as esign_service


def _request(*signers, enforce_signing_order=False, expires_at=None, status="sent"):
    req = SignatureRequest(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        status=status,
        provider="internal",
        enforce_signing_order=enforce_signing_order,
        expires_at=expires_at,
    )
    req.signers = list(signers)
    return req


def _signer(order, status="pending"):
    return SignatureSigner(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        name=f"Signer {order}",
        email=f"signer{order}@example.com",
        role="signer",
        sign_order=order,
        status=status,
    )


def test_mark_request_expired_only_closes_open_requests():
    req = _request(
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        status="sent",
    )

    assert mark_request_expired_if_needed(req) is True
    assert req.status == "expired"

    completed = _request(
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        status="completed",
    )

    assert mark_request_expired_if_needed(completed) is False
    assert completed.status == "completed"


def test_enforced_signing_order_allows_only_next_pending_group():
    first = _signer(0)
    second = _signer(1)
    req = _request(first, second, enforce_signing_order=True)

    assert next_pending_signers(req) == [first]
    assert signer_can_act_now(req, first) is True
    assert signer_can_act_now(req, second) is False

    first.status = "signed"

    assert next_pending_signers(req) == [second]
    assert signer_can_act_now(req, second) is True


@pytest.mark.asyncio
async def test_record_portal_decline_closes_request_with_reason():
    signer = _signer(0)
    req = _request(signer)

    await record_portal_decline(
        req,
        signer,
        reason="Need attorney changes",
        ip="203.0.113.10",
    )

    assert req.status == "declined"
    assert req.decline_reason == "Need attorney changes"
    assert req.declined_at is not None
    assert signer.status == "declined"
    assert signer.decline_reason == "Need attorney changes"
    assert signer.audit["method"] == "portal_decline"


@pytest.mark.asyncio
async def test_signature_audit_records_explicit_consent_and_client_evidence():
    signer = _signer(0)
    await record_portal_signature(
        signer,
        typed_signature="Signer 0",
        ip="203.0.113.10",
        consent_text_version="clarity-esign-consent-v1",
        user_agent="Test Browser",
    )
    assert signer.audit["consent_to_electronic_signature"] is True
    assert signer.audit["consent_text_version"] == "clarity-esign-consent-v1"
    assert signer.audit["user_agent"] == "Test Browser"


@pytest.mark.asyncio
async def test_bound_source_detects_document_mutation(monkeypatch):
    state = {"matches": True}

    class DB:
        async def get(self, _model, _id):
            return SimpleNamespace(
                tenant_id=req.tenant_id,
                matter_id=req.matter_id,
                file_size=len(b"version one"),
            )

    async def fake_read(**kwargs):
        assert kwargs["tenant_id"] == str(req.tenant_id)
        assert kwargs["expected_sha256"] == req.source_document_sha256
        if not state["matches"]:
            raise MatterFileIntegrityError("mutated")
        return b"version one"

    monkeypatch.setattr(
        esignature_router.matter_file_store,
        "read_matter_file_bytes",
        fake_read,
    )

    req = _request()
    req.document_id = uuid.uuid4()
    req.source_document_sha256 = hashlib.sha256(b"version one").hexdigest()
    req.source_document_size = len(b"version one")
    assert await _source_document_is_unchanged(DB(), req) is True
    state["matches"] = False
    assert await _source_document_is_unchanged(DB(), req) is False


@pytest.mark.asyncio
async def test_two_completions_create_distinct_immutable_evidence_with_full_metadata(
    monkeypatch,
):
    tenant_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    source_id = uuid.uuid4()
    source = SimpleNamespace(
        id=source_id,
        tenant_id=tenant_id,
        matter_id=matter_id,
        filename="Client Agreement.pdf",
    )
    matter = SimpleNamespace(
        id=matter_id,
        tenant_id=tenant_id,
        slug="client-agreement",
        matter_name="Client Agreement",
        cloud_folder={"google_drive": {"id": "matter-folder"}},
    )

    def completed_request(request_id):
        signer = SimpleNamespace(
            status="signed",
            sign_order=0,
            audit={"method": "portal_typed", "request": str(request_id)},
            name="Client Signer",
            email="client@example.com",
            typed_signature="Client Signer",
            signed_at=datetime.now(timezone.utc),
            signed_ip="203.0.113.10",
        )
        return SimpleNamespace(
            id=request_id,
            tenant_id=tenant_id,
            matter_id=matter_id,
            document_id=source_id,
            status="partially_signed",
            signers=[signer],
            source_document_sha256=hashlib.sha256(b"source").hexdigest(),
            source_document_size=len(b"source"),
            evidence_sha256=None,
            completion_artifact_sha256=None,
            provider_envelope_id=None,
            completed_at=None,
        )

    class DB:
        def __init__(self):
            self.documents = {source_id: source}
            self.added = []

        async def get(self, model, row_id):
            assert model is MatterDocument
            return self.documents.get(row_id)

        def add(self, row):
            self.added.append(row)
            if isinstance(row, MatterDocument):
                self.documents[row.id] = row

    uploads = []

    async def fake_store(**kwargs):
        uploads.append(kwargs)
        index = len(uploads)
        return StorageResult(
            provider="google",
            backend="google_drive",
            storage_path=f"https://drive.google.com/file/d/evidence-{index}/view",
            web_url=f"https://drive.google.com/file/d/evidence-{index}/view",
            provider_item_id=f"evidence-{index}",
            drive_id=f"drive-{index}",
            parent_id="matter-folder",
        )

    monkeypatch.setattr(
        esign_service._file_store,
        "store_matter_file_result",
        fake_store,
    )
    db = DB()
    first_request = completed_request(uuid.uuid4())
    second_request = completed_request(uuid.uuid4())

    first = await complete_request_if_done(db, first_request, matter)
    second = await complete_request_if_done(db, second_request, matter)

    assert first is not None and second is not None
    assert first.filename != second.filename
    assert first_request.id.hex in first.filename
    assert second_request.id.hex in second.filename
    assert first_request.completion_artifact_sha256[:16] in first.filename
    assert second_request.completion_artifact_sha256[:16] in second.filename
    assert [upload["filename"] for upload in uploads] == [
        first.filename,
        second.filename,
    ]
    assert len({upload["filename"] for upload in uploads}) == 2

    assert first.storage_provider == "google"
    assert first.storage_backend == "google_drive"
    assert first.provider_object_id == "evidence-1"
    assert first.provider_drive_id == "drive-1"
    assert first.provider_parent_id == "matter-folder"
    assert second.provider_object_id == "evidence-2"
    assert first_request.provider_envelope_id == str(first.id)
    assert second_request.provider_envelope_id == str(second.id)

    # A retry of an already completed request returns the original artifact and
    # cannot upload or overwrite evidence again.
    assert await complete_request_if_done(db, first_request, matter) is first
    assert len(uploads) == 2


def test_portal_signature_consent_is_fail_closed_by_default():
    body = PortalSignRequest(typed_signature="Signer")
    assert body.consent_to_electronic_signature is False
