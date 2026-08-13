"""Tests for document upload and listing."""

import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.routers.documents import _persist_uploaded_document
from app.routers.documents import _is_allowed_upload


class RecordingSession:
    def __init__(self):
        self.calls = []

    def add(self, doc):
        self.calls.append("add")

    async def flush(self):
        self.calls.append("flush")

    async def refresh(self, doc):
        self.calls.append("refresh")

    async def commit(self):
        self.calls.append("commit")


@pytest.mark.asyncio
async def test_upload_persistence_refreshes_before_commit_for_rls_context():
    session = RecordingSession()
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        filename="test.txt",
        content_type="text/plain",
        file_size=4,
        status="pending",
        chunk_count=0,
        created_at=datetime.now(timezone.utc),
        indexed_at=None,
        source_modified_at=None,
        embedding_model=None,
        embedding_version=None,
        error_message=None,
    )

    await _persist_uploaded_document(session, doc)

    assert session.calls == ["add", "flush", "refresh", "commit"]


@pytest.mark.asyncio
async def test_list_documents_empty(client: AsyncClient):
    resp = await client.get("/api/documents")
    assert resp.status_code == 200
    data = resp.json()
    assert "documents" in data
    assert isinstance(data["documents"], list)


@pytest.mark.asyncio
async def test_upload_text_document(client: AsyncClient, mock_embeddings):
    content = b"This is a test legal document about contract law.\nSmith v. Jones, 123 F.3d 456."
    resp = await client.post(
        "/api/documents/upload",
        files={"file": ("test_brief.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert "id" in data
    assert data["filename"] == "test_brief.txt"
    assert data["status"] in ("pending", "processing", "ready")


def test_general_document_upload_rejects_legacy_doc():
    assert not _is_allowed_upload("agreement.doc", "application/msword")
    assert _is_allowed_upload(
        "agreement.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@pytest.mark.asyncio
async def test_document_status_endpoint(client: AsyncClient, mock_embeddings):
    content = b"Legal document content here."
    upload = (
        await client.post(
            "/api/documents/upload",
            files={"file": ("status_test.txt", io.BytesIO(content), "text/plain")},
        )
    ).json()

    status_resp = await client.get(f"/api/documents/{upload['id']}/status")
    assert status_resp.status_code == 200
    assert "status" in status_resp.json()
