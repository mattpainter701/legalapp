import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.mediation_service import (
    assert_proposal_integrity,
    case_document_download_response,
    proposal_content_sha256,
)
from app.services.plugin_entitlements import plugin_entitlement_is_active


def test_mediation_entitlement_requires_an_active_bounded_state():
    now = datetime.now(timezone.utc)
    assert plugin_entitlement_is_active(
        SimpleNamespace(
            status="purchased",
            starts_at=now - timedelta(days=1),
            expires_at=None,
        ),
        now=now,
    )
    assert plugin_entitlement_is_active(
        SimpleNamespace(
            status="trial",
            starts_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=1),
        ),
        now=now,
    )
    for entitlement in (
        None,
        SimpleNamespace(status="trial", starts_at=None, expires_at=None),
        SimpleNamespace(
            status="trial",
            starts_at=None,
            expires_at=now - timedelta(seconds=1),
        ),
        SimpleNamespace(
            status="purchased",
            starts_at=now + timedelta(seconds=1),
            expires_at=None,
        ),
        SimpleNamespace(status="disabled", starts_at=None, expires_at=None),
    ):
        assert not plugin_entitlement_is_active(entitlement, now=now)


def test_proposal_integrity_binds_text_and_parent_lineage():
    parent_id = uuid4()
    proposal = SimpleNamespace(
        title="Opening allocation",
        body="Party A receives the residence.",
        parent_proposal_id=parent_id,
    )
    proposal.content_sha256 = proposal_content_sha256(
        title=proposal.title,
        body=proposal.body,
        parent_proposal_id=proposal.parent_proposal_id,
    )

    assert_proposal_integrity(proposal)
    proposal.body = "Changed after review"
    with pytest.raises(HTTPException) as exc_info:
        assert_proposal_integrity(proposal)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_document_download_fails_closed_after_byte_tampering(tmp_path):
    content = b"reviewed mediation exhibit"
    path = tmp_path / "exhibit.pdf"
    path.write_bytes(content)
    document = SimpleNamespace(
        storage_path=str(path),
        filename="exhibit.pdf",
        content_type="application/pdf",
        content_sha256=hashlib.sha256(content).hexdigest(),
    )

    response = await case_document_download_response(document)
    assert response.body == content
    assert "attachment" in response.headers["content-disposition"]

    path.write_bytes(b"replacement bytes")
    with pytest.raises(HTTPException) as exc_info:
        await case_document_download_response(document)
    assert exc_info.value.status_code == 409
