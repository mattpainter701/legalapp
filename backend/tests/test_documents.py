"""Tests for document upload and listing."""

import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.routers.documents import _document_to_response, _persist_uploaded_document
from app.routers.documents import _is_allowed_upload
from app.routers import documents as documents_router
from app.models.conversation import Conversation
from app.models.document import Chunk, Document
from app.models.user import User


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
    assert upload["processing_job_id"]
    job_response = await client.get(
        f"/api/documents/jobs/{upload['processing_job_id']}"
    )
    assert job_response.status_code == 200
    assert job_response.json()["kind"] == "document_ingest"
    assert "status" in status_resp.json()


@pytest.mark.asyncio
async def test_chat_attachment_download_is_available_to_conversation_owner(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(documents_router.settings, "UPLOAD_DIR", str(tmp_path))
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        title="Private deal chat",
    )
    document_id = uuid.uuid4()
    file_path = tmp_path / str(test_tenant.id) / str(document_id) / "project-atlas-loi.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"Project Atlas synthetic LOI")
    document = Document(
        id=document_id,
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        conversation_id=conversation.id,
        filename="project-atlas-loi.txt",
        content_type="text/plain",
        file_size=file_path.stat().st_size,
        storage_path=str(file_path),
        status="ready",
        chunk_count=0,
    )
    db_session.add_all([conversation, document])
    await db_session.commit()

    response = await client.get(f"/api/documents/{document.id}/download")

    assert response.status_code == 200
    assert response.content == b"Project Atlas synthetic LOI"
    assert "attachment" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_chat_attachment_download_is_hidden_from_other_tenant_user(
    client: AsyncClient,
    db_session,
    test_tenant,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(documents_router.settings, "UPLOAD_DIR", str(tmp_path))
    other_user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="other-attorney@example.com",
        full_name="Other Attorney",
        role="user",
        is_active=True,
    )
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=other_user.id,
        title="Another user's private deal chat",
    )
    document_id = uuid.uuid4()
    file_path = tmp_path / str(test_tenant.id) / str(document_id) / "private-board-consent.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"Private synthetic board consent")
    document = Document(
        id=document_id,
        tenant_id=test_tenant.id,
        user_id=other_user.id,
        conversation_id=conversation.id,
        filename="private-board-consent.txt",
        content_type="text/plain",
        file_size=file_path.stat().st_size,
        storage_path=str(file_path),
        status="ready",
        chunk_count=0,
    )
    db_session.add(other_user)
    await db_session.flush()
    db_session.add_all([conversation, document])
    await db_session.commit()

    response = await client.get(f"/api/documents/{document.id}/download")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_document_delete_advances_corpus_revision_after_commit(
    client,
    db_session,
    test_tenant,
    test_user,
    tmp_path,
):
    file_path = tmp_path / "obsolete-contract.txt"
    file_path.write_text("Obsolete contract version.", encoding="utf-8")
    document = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        filename=file_path.name,
        content_type="text/plain",
        file_size=file_path.stat().st_size,
        storage_path=str(file_path),
        status="ready",
    )
    db_session.add(document)
    await db_session.commit()
    await db_session.refresh(test_tenant)
    starting_revision = test_tenant.rag_corpus_revision

    response = await client.delete(f"/api/documents/{document.id}")

    assert response.status_code == 204
    await db_session.refresh(test_tenant)
    assert test_tenant.rag_corpus_revision == starting_revision + 1
    assert await db_session.get(Document, document.id) is None
    assert file_path.exists() is False


@pytest.mark.asyncio
async def test_indexing_without_an_embedding_provider_stays_keyword_searchable(
    monkeypatch, test_tenant, db_session, tmp_path
):
    """A missing embedding provider must not make documents invisible to RAG.

    Retrieval is hybrid (pgvector + Postgres FTS) and the FTS half needs only
    the chunk rows. Failing the whole document left tenants with zero
    retrievable sources and no citations, which is worse than keyword-only
    recall.
    """
    from app.models.document import Chunk
    from app.routers import documents as documents_router

    file_path = tmp_path / "engagement-letter.txt"
    file_path.write_bytes(
        b"Redwood Outdoor Supply outside general counsel engagement letter. "
        b"The included retainer covers forty-five attorney hours each month."
    )
    document = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        filename="engagement-letter.txt",
        content_type="text/plain",
        file_size=file_path.stat().st_size,
        storage_path=str(file_path),
        status="pending",
        chunk_count=0,
    )
    db_session.add(document)
    await db_session.commit()
    await db_session.refresh(test_tenant)
    starting_revision = test_tenant.rag_corpus_revision

    async def no_embeddings(_texts):
        return []

    monkeypatch.setattr(
        documents_router.embedding_service, "embed_batch", no_embeddings
    )

    await documents_router._process_document(str(document.id), str(test_tenant.id))

    await db_session.refresh(document)
    await db_session.refresh(test_tenant)
    assert document.status == "ready"
    assert test_tenant.rag_corpus_revision == starting_revision + 1
    assert document.chunk_count > 0
    # A null embedding_model on a ready document is the keyword-only signal.
    assert document.embedding_model is None
    assert document.embedding_version is None

    response = _document_to_response(document)
    assert response.retrieval_mode == "keyword_only"
    assert "keyword only" in response.indexing_warning.lower()

    chunks = (
        (
            await db_session.execute(
                select(Chunk).where(Chunk.document_id == document.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(chunks) == document.chunk_count
    assert all(chunk.embedding is None for chunk in chunks)
    assert any("forty-five attorney hours" in chunk.content for chunk in chunks)


@pytest.mark.asyncio
async def test_platform_operator_can_queue_degraded_document_reindex(
    client, db_session, test_tenant, tmp_path
):
    from app.models.durable_job import DurableJob
    from tests.platform_auth_helpers import platform_headers

    starting_revision = test_tenant.rag_corpus_revision

    file_path = tmp_path / "existing-demo-contract.txt"
    file_path.write_text("Synthetic merger consent language.", encoding="utf-8")
    document = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        filename=file_path.name,
        content_type="text/plain",
        file_size=file_path.stat().st_size,
        storage_path=str(file_path),
        status="ready",
        chunk_count=1,
        embedding_model=None,
    )
    db_session.add(document)
    await db_session.flush()
    db_session.add(
        Chunk(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            document_id=document.id,
            content="Old keyword-only chunk.",
            chunk_index=0,
            embedding=None,
        )
    )
    await db_session.commit()

    response = await client.post(
        "/api/platform/documents/reindex",
        headers=platform_headers(["platform:write"]),
        json={"tenant_id": str(test_tenant.id), "only_degraded": True},
    )

    assert response.status_code == 200
    await db_session.refresh(test_tenant)
    assert test_tenant.rag_corpus_revision == starting_revision + 1
    payload = response.json()
    assert payload["selected_count"] == 1
    assert payload["queued_count"] == 1
    refreshed = await db_session.get(Document, document.id)
    await db_session.refresh(refreshed)
    assert refreshed.status == "pending"
    assert refreshed.chunk_count == 0
    assert refreshed.embedding_model is None
    chunks = list(
        (
            await db_session.scalars(
                select(Chunk).where(Chunk.document_id == document.id)
            )
        ).all()
    )
    assert chunks == []
    job = await db_session.scalar(
        select(DurableJob).where(DurableJob.id == uuid.UUID(payload["job_ids"][0]))
    )
    assert job is not None
    assert job.kind == "document_ingest"
    assert job.payload == {"document_id": str(document.id)}
