"""Tests for document upload and listing."""

import io

import pytest
from httpx import AsyncClient


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
