import logging
import os
import uuid

import aiofiles
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    UploadFile,
    File,
)
from fastapi.responses import FileResponse, RedirectResponse
import asyncio
import hashlib
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context, async_session_maker
from app.middleware.tenant import get_current_user
from app.models.document import Document, Chunk
from app.models.conversation import Conversation
from app.schemas.document import DocumentResponse, DocumentList
from app.services.embeddings import EmbeddingService
from app.services.durable_jobs import enqueue_job, get_tenant_job, serialize_job
from app.utils.text_processing import chunk_text, extract_text

settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

embedding_service = EmbeddingService()

# Only formats `extract_text` actually knows how to parse. Anything else falls
# through to a raw UTF-8 decode there, which happily "succeeds" (with garbage)
# on arbitrary binaries — so unlisted types are rejected here instead of being
# silently ingested into the RAG pipeline.
_ALLOWED_UPLOAD_EXTENSIONS = (".pdf", ".docx", ".txt")
_ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}


def _is_allowed_upload(filename: str, content_type: str | None) -> bool:
    fn_lower = filename.lower()
    if fn_lower.endswith(_ALLOWED_UPLOAD_EXTENSIONS):
        return True
    return bool(content_type) and content_type.lower() in _ALLOWED_UPLOAD_CONTENT_TYPES


def _document_to_response(doc: Document) -> DocumentResponse:
    retrieval_mode = "not_indexed"
    indexing_warning = None
    if doc.status in {"ready", "indexed"} and doc.chunk_count:
        if doc.embedding_model:
            retrieval_mode = "semantic_hybrid"
        else:
            retrieval_mode = "keyword_only"
            indexing_warning = (
                "Semantic retrieval is unavailable; this document is searchable "
                "by keyword only."
            )
    return DocumentResponse(
        id=str(doc.id),
        filename=doc.filename,
        content_type=doc.content_type,
        file_size=doc.file_size,
        status=doc.status,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        indexed_at=doc.indexed_at,
        source_modified_at=doc.source_modified_at,
        embedding_model=doc.embedding_model,
        embedding_version=doc.embedding_version,
        retrieval_mode=retrieval_mode,
        indexing_warning=indexing_warning,
        indexing_error=doc.error_message if doc.status == "error" else None,
    )


async def _persist_uploaded_document(
    db: AsyncSession, doc: Document
) -> DocumentResponse:
    """Persist upload row while tenant RLS context is still active."""
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    response = _document_to_response(doc)
    await db.commit()
    return response


async def _commit_and_restore_tenant_context(db: AsyncSession, tenant_id: str) -> None:
    await db.commit()
    await set_tenant_context(db, tenant_id)


async def _process_document(document_id: str, tenant_id: str) -> None:
    """
    Background task: extract text, chunk, embed, and store chunks.
    Uses a fresh DB session since this runs outside the request context.
    Offloads CPU-bound work (text extraction, chunking) to thread pool via asyncio.to_thread().
    """
    async with async_session_maker() as db:
        try:
            await set_tenant_context(db, tenant_id)
            # Fetch the document
            result = await db.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()

            if doc is None:
                return

            # Update status to processing
            doc.status = "processing"
            doc.error_message = None
            doc.indexed_at = None
            await _commit_and_restore_tenant_context(db, tenant_id)

            # Read file bytes
            if not doc.storage_path or not os.path.exists(doc.storage_path):
                doc.status = "error"
                doc.error_message = "File not found on disk"
                await db.commit()
                return

            async with aiofiles.open(doc.storage_path, "rb") as f:
                file_bytes = await f.read()

            # Extract text (CPU-bound) in thread pool
            text = await asyncio.to_thread(
                extract_text,
                file_bytes=file_bytes,
                content_type=doc.content_type or "",
                filename=doc.filename,
            )

            if not text or not text.strip():
                doc.status = "error"
                doc.error_message = "Could not extract text from document"
                await db.commit()
                return

            doc.content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

            # Chunk text (CPU-bound) in thread pool
            chunks_text = await asyncio.to_thread(chunk_text, text, 500, 50)

            if not chunks_text:
                doc.status = "error"
                doc.error_message = "Document produced no text chunks"
                await db.commit()
                return

            # Embed chunks in batches. Retrieval is hybrid (pgvector + Postgres
            # FTS), and the FTS half needs only the chunk rows. When no
            # embedding provider is configured, persisting the text keeps the
            # document searchable and citable instead of discarding it — a
            # partially-indexed document is far better than an invisible one.
            embeddings = await embedding_service.embed_batch(chunks_text)
            keyword_only = len(embeddings) == 0

            # Insert chunks
            chunk_objects = []
            for idx, chunk_content in enumerate(chunks_text):
                chunk_obj = Chunk(
                    id=uuid.uuid4(),
                    tenant_id=uuid.UUID(tenant_id),
                    document_id=doc.id,
                    content=chunk_content,
                    chunk_index=idx,
                    embedding=None if keyword_only else embeddings[idx],
                )
                chunk_objects.append(chunk_obj)

            db.add_all(chunk_objects)

            # Update document status
            doc.status = "ready"
            doc.chunk_count = len(chunk_objects)
            doc.indexed_at = datetime.now(timezone.utc)
            doc.embedding_model = None if keyword_only else embedding_service.model
            doc.embedding_version = None if keyword_only else 1
            if keyword_only:
                # A null embedding_model on a ready document is the signal that
                # retrieval is keyword-only. Log it too: silently demoing
                # keyword search as if it were semantic retrieval is worse than
                # the degraded result itself.
                logger.warning(
                    "Document %s indexed keyword-only: no embedding provider is "
                    "configured, so semantic retrieval is unavailable",
                    document_id,
                )
            await db.commit()

        except Exception as exc:
            await db.rollback()
            # Attempt to mark document as errored
            try:
                await set_tenant_context(db, tenant_id)
                result = await db.execute(
                    select(Document).where(Document.id == document_id)
                )
                doc = result.scalar_one_or_none()
                if doc:
                    doc.status = "error"
                    doc.error_message = str(exc)[:500]
                    await db.commit()
            except Exception:
                pass


@router.get("", response_model=DocumentList)
async def list_documents(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all documents for the current tenant."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Document)
        .where(
            Document.tenant_id == user.tenant_id,
            Document.conversation_id.is_(None),
        )
        .order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()

    return DocumentList(
        documents=[_document_to_response(d) for d in docs],
        total=len(docs),
    )


@router.post("/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a document for processing.
    Returns immediately after saving; processing happens in background.
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # Validate file size
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    file_bytes = await file.read()

    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    if not _is_allowed_upload(file.filename, file.content_type):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: PDF, DOCX, TXT.",
        )

    # Create storage directory
    document_id = uuid.uuid4()
    storage_dir = os.path.join(
        settings.UPLOAD_DIR, str(user.tenant_id), str(document_id)
    )
    os.makedirs(storage_dir, exist_ok=True)

    storage_path = os.path.join(storage_dir, os.path.basename(file.filename))

    # Save file to disk
    async with aiofiles.open(storage_path, "wb") as out_file:
        await out_file.write(file_bytes)

    # Create Document record
    doc = Document(
        id=document_id,
        tenant_id=user.tenant_id,
        user_id=user.id,
        filename=file.filename,
        content_type=file.content_type,
        file_size=len(file_bytes),
        storage_path=storage_path,
        status="pending",
        chunk_count=0,
    )
    response = await _persist_uploaded_document(db, doc)

    job = await enqueue_job(
        db,
        tenant_id=user.tenant_id,
        kind="document_ingest",
        idempotency_key=str(document_id),
        payload={"document_id": str(document_id)},
    )
    await db.commit()
    response.processing_job_id = str(job.id)

    return response


@router.get("/jobs/{job_id}")
async def get_document_job(
    job_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    job = await get_tenant_job(db, user.tenant_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job(job)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and all its chunks."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == user.tenant_id,
        )
    )
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete file from disk if it exists
    if doc.storage_path and os.path.exists(doc.storage_path):
        try:
            os.remove(doc.storage_path)
            # Clean up directory if empty
            parent_dir = os.path.dirname(doc.storage_path)
            if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                os.rmdir(parent_dir)
        except OSError:
            pass

    # Chunks are cascade-deleted via FK, but we delete explicitly for clarity
    await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    await db.delete(doc)
    await db.commit()


@router.get("/{document_id}/status", response_model=DocumentResponse)
async def get_document_status(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get document processing status."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == user.tenant_id,
        )
    )
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return _document_to_response(doc)


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Download a tenant document without bypassing private-chat ownership.

    Tenant-library documents (``conversation_id IS NULL``) follow the same
    tenant visibility as ``GET /api/documents``. Session attachments remain
    private to the user who owns the linked conversation.
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == user.tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.conversation_id is not None:
        conversation_owner = await db.scalar(
            select(Conversation.user_id).where(
                Conversation.id == doc.conversation_id,
                Conversation.tenant_id == user.tenant_id,
            )
        )
        if conversation_owner is None or str(conversation_owner) != str(user.id):
            # Preserve the same non-enumerating behavior as a missing document.
            raise HTTPException(status_code=404, detail="Document not found")

    if doc.storage_path and doc.storage_path.startswith(("http://", "https://")):
        return RedirectResponse(doc.storage_path)
    if not doc.storage_path or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=doc.storage_path,
        filename=doc.filename,
        media_type=doc.content_type or "application/octet-stream",
    )
