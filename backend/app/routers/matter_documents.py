"""Router for matter file attachments (case documents)."""

import os
import uuid

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.schemas.matter_document import (
    MatterDocumentListResponse,
    MatterDocumentResponse,
    MatterDocumentUpdate,
)

settings = get_settings()
router = APIRouter(prefix="/api", tags=["matter-documents"])


async def _get_matter_or_404(
    matter_id: str, tenant_id: uuid.UUID, db: AsyncSession
) -> Matter:
    """Fetch a matter ensuring it belongs to the current tenant."""
    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


async def _get_doc_or_404(
    doc_id: str, matter_id: str, tenant_id: uuid.UUID, db: AsyncSession
) -> MatterDocument:
    result = await db.execute(
        select(MatterDocument).where(
            MatterDocument.id == doc_id,
            MatterDocument.matter_id == matter_id,
            MatterDocument.tenant_id == tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get(
    "/matters/{matter_id}/documents",
    response_model=MatterDocumentListResponse,
)
async def list_matter_documents(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all documents attached to a matter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)

    result = await db.execute(
        select(MatterDocument)
        .where(
            MatterDocument.matter_id == matter_id,
            MatterDocument.tenant_id == user.tenant_id,
        )
        .order_by(MatterDocument.created_at.desc())
    )
    docs = result.scalars().all()
    return MatterDocumentListResponse(
        items=[MatterDocumentResponse.model_validate(d) for d in docs],
        total=len(docs),
    )


@router.post(
    "/matters/{matter_id}/documents/upload",
    response_model=MatterDocumentResponse,
    status_code=201,
)
async def upload_matter_document(
    matter_id: str,
    request: Request,
    file: UploadFile = File(...),
    description: str | None = Form(None),
    document_category: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file attachment to a matter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    file_bytes = await file.read()
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
        )

    doc_id = uuid.uuid4()
    storage_dir = os.path.join(
        settings.UPLOAD_DIR,
        str(user.tenant_id),
        "matters",
        matter_id,
        str(doc_id),
    )
    os.makedirs(storage_dir, exist_ok=True)
    storage_path = os.path.join(storage_dir, file.filename)

    async with aiofiles.open(storage_path, "wb") as out_file:
        await out_file.write(file_bytes)

    doc = MatterDocument(
        id=doc_id,
        tenant_id=user.tenant_id,
        matter_id=uuid.UUID(matter_id),
        uploaded_by_user_id=user.id,
        filename=file.filename,
        content_type=file.content_type,
        file_size=len(file_bytes),
        storage_path=storage_path,
        description=description,
        document_category=document_category,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return MatterDocumentResponse.model_validate(doc)


@router.patch(
    "/matters/{matter_id}/documents/{doc_id}",
    response_model=MatterDocumentResponse,
)
async def update_matter_document(
    matter_id: str,
    doc_id: str,
    body: MatterDocumentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update description or category of an attached document."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    doc = await _get_doc_or_404(doc_id, matter_id, user.tenant_id, db)

    if body.description is not None:
        doc.description = body.description
    if body.document_category is not None:
        doc.document_category = body.document_category

    await db.commit()
    await db.refresh(doc)
    return MatterDocumentResponse.model_validate(doc)


@router.delete("/matters/{matter_id}/documents/{doc_id}", status_code=204)
async def delete_matter_document(
    matter_id: str,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete an attached document and its file from disk."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    doc = await _get_doc_or_404(doc_id, matter_id, user.tenant_id, db)

    if doc.storage_path and os.path.exists(doc.storage_path):
        try:
            os.remove(doc.storage_path)
            parent_dir = os.path.dirname(doc.storage_path)
            if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                os.rmdir(parent_dir)
        except OSError:
            pass

    await db.delete(doc)
    await db.commit()


@router.get("/matters/{matter_id}/documents/{doc_id}/download")
async def download_matter_document(
    matter_id: str,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Stream the file for download."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    doc = await _get_doc_or_404(doc_id, matter_id, user.tenant_id, db)

    if not doc.storage_path or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=doc.storage_path,
        filename=doc.filename,
        media_type=doc.content_type or "application/octet-stream",
    )
