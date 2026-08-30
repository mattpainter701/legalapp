import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.mediation_service import (
    assert_proposal_integrity,
    case_document_download_response,
    proposal_content_sha256,
)


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
