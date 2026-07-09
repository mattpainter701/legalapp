from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.models.signature import SignatureRequest, SignatureSigner
from app.routers.esignature import _source_document_is_unchanged
from app.schemas.signature import PortalSignRequest
from app.services.esign.service import (
    mark_request_expired_if_needed,
    next_pending_signers,
    record_portal_decline,
    signer_can_act_now,
    record_portal_signature,
)


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
async def test_bound_source_detects_document_mutation(tmp_path):
    path = tmp_path / "agreement.txt"
    path.write_bytes(b"version one")

    class Doc:
        storage_path = str(path)

    class DB:
        async def get(self, _model, _id):
            return Doc()

    import hashlib

    req = _request()
    req.document_id = uuid.uuid4()
    req.source_document_sha256 = hashlib.sha256(b"version one").hexdigest()
    assert await _source_document_is_unchanged(DB(), req) is True
    path.write_bytes(b"version two")
    assert await _source_document_is_unchanged(DB(), req) is False


def test_portal_signature_consent_is_fail_closed_by_default():
    body = PortalSignRequest(typed_signature="Signer")
    assert body.consent_to_electronic_signature is False
