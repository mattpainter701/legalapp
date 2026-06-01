import os
import uuid

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, BackgroundTasks
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context, async_session_maker
from app.middleware.tenant import get_current_user
from app.models.document import Document, Chunk
from app.schemas.document import DocumentResponse, DocumentList
from app.services.embeddings import EmbeddingService
from app.utils.text_processing import chunk_text, extract_text

settings = get_settings()
router = APIRouter(prefix="/documents", tags=["documents"])

embedding_service = EmbeddingService()


def _document_to_response(doc: Document) -> DocumentResponse:
    return DocumentResponse(
        id=str(doc.id),
        filename=doc.filename,
        content_type=doc.content_type,
        file_size=doc.file_size,
        status=doc.status,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
    )


async def _process_document(document_id: str, tenant_id: str) -> None:
    """
    Background task: extract text, chunk, embed, and store chunks.
    Uses a fresh DB session since this runs outside the request context.
    """
    async with async_session_maker() as db:
        try:
            # Fetch the document
            result = await db.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()

            if doc is None:
                return

            # Update status to processing
            doc.status = "processing"
            await db.commit()

            # Read file bytes
            if not doc.storage_path or not os.path.exists(doc.storage_path):
                doc.status = "error"
                doc.error_message = "File not found on disk"
                await db.commit()
                return

            async with aiofiles.open(doc.storage_path, "rb") as f:
                file_bytes = await f.read()

            # Extract text
            text = await extract_text(
                file_bytes=file_bytes,
                content_type=doc.content_type or "",
                filename=doc.filename,
            )

            if not text or not text.strip():
                doc.status = "error"
                doc.error_message = "Could not extract text from document"
                await db.commit()
                return

            # Chunk text
            chunks_text = chunk_text(text, chunk_size=500, overlap=50)

            if not chunks_text:
                doc.status = "error"
                doc.error_message = "Document produced no text chunks"
                await db.commit()
                return

            # Embed chunks in batches
            embeddings = await embedding_service.embed_batch(chunks_text)

            # Insert chunks
            chunk_objects = []
            for idx, (chunk_content, embedding) in enumerate(
                zip(chunks_text, embeddings)
            ):
                chunk_obj = Chunk(
                    id=uuid.uuid4(),
                    tenant_id=uuid.UUID(tenant_id),
                    document_id=doc.id,
                    content=chunk_content,
                    chunk_index=idx,
                    embedding=embedding,
                )
                chunk_objects.append(chunk_obj)

            db.add_all(chunk_objects)

            # Update document status
            doc.status = "ready"
            doc.chunk_count = len(chunk_objects)
            await db.commit()

        except Exception as exc:
            await db.rollback()
            # Attempt to mark document as errored
            try:
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
        .where(Document.tenant_id == user.tenant_id)
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
    background_tasks: BackgroundTasks,
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

    # Create storage directory
    document_id = uuid.uuid4()
    storage_dir = os.path.join(
        settings.UPLOAD_DIR, str(user.tenant_id), str(document_id)
    )
    os.makedirs(storage_dir, exist_ok=True)

    storage_path = os.path.join(storage_dir, file.filename)

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
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # Launch background processing task
    background_tasks.add_task(
        _process_document,
        document_id=str(document_id),
        tenant_id=str(user.tenant_id),
    )

    return _document_to_response(doc)


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
    await db.execute(
        delete(Chunk).where(Chunk.document_id == doc.id)
    )
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
