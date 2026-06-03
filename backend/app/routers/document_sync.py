import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user, require_admin
from app.schemas.document_sync import (
    DocumentSyncRequest,
    DocumentSyncResponse,
    DocumentSyncStats,
    SyncedDocument,
)
from app.services.document_sync import document_sync

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync/documents", tags=["document-sync"])


@router.get("/stats", response_model=DocumentSyncStats)
async def get_doc_sync_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)
    stats = await document_sync.get_sync_stats(db, tenant_id, str(user.id))

    return DocumentSyncStats(
        onedrive=stats.get("onedrive", 0),
        sharepoint=stats.get("sharepoint", 0),
        google_drive=stats.get("google_drive", 0),
    )


@router.post("/list", response_model=DocumentSyncResponse)
async def list_documents(
    body: DocumentSyncRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)

    if body.provider == "onedrive":
        docs = await document_sync.sync_onedrive(
            db,
            tenant_id,
            str(user.id) if not body.user_id else body.user_id,
            max_files=body.max_files,
        )
    elif body.provider == "sharepoint":
        docs = await document_sync.sync_sharepoint(
            db, tenant_id, site_id=body.site_id, max_files=body.max_files
        )
    elif body.provider == "google_drive":
        docs = await document_sync.sync_google_drive(
            db,
            tenant_id,
            str(user.id) if not body.user_id else body.user_id,
            max_files=body.max_files,
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Unknown provider: {body.provider}"
        )

    downloaded = 0
    if body.download_and_index:
        for doc in docs:
            try:
                path = await document_sync.download_and_process(
                    db, tenant_id, doc, str(user.id)
                )
                if path:
                    downloaded += 1
                    logger.info("Downloaded %s → %s", doc["name"], path)
            except Exception as exc:
                logger.warning("Failed to download %s: %s", doc["name"], exc)

    return DocumentSyncResponse(
        provider=body.provider,
        documents_found=len(docs),
        documents_downloaded=downloaded,
        documents=[
            SyncedDocument(
                id=d["id"],
                name=d["name"],
                size=d["size"],
                modified=d.get("modified"),
                url=d.get("url"),
                drive=d["drive"],
                mime_type=d.get("mime_type"),
            )
            for d in docs
        ],
    )


@router.post("/sync-and-ingest")
async def sync_and_ingest(
    body: DocumentSyncRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Admin-initiated: sync documents and run through RAG ingestion pipeline."""
    await require_admin(request, db)
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="No tenant context")

    await set_tenant_context(db, tenant_id)

    async def _ingest():
        from app.database import async_session_maker
        from app.models.document import Document
        from app.routers.documents import _process_document
        import uuid as _uuid

        async with async_session_maker() as session:
            try:
                await set_tenant_context(session, tenant_id)
                if body.provider == "onedrive":
                    docs = await document_sync.sync_onedrive(
                        session, tenant_id, max_files=body.max_files
                    )
                elif body.provider == "sharepoint":
                    docs = await document_sync.sync_sharepoint(
                        session,
                        tenant_id,
                        site_id=body.site_id,
                        max_files=body.max_files,
                    )
                elif body.provider == "google_drive":
                    docs = await document_sync.sync_google_drive(
                        session, tenant_id, body.user_id, max_files=body.max_files
                    )
                else:
                    return

                for doc in docs:
                    try:
                        local_path = await document_sync.download_and_process(
                            session, tenant_id, doc
                        )
                        if local_path:
                            file_size = Path(local_path).stat().st_size
                            doc_record = Document(
                                id=_uuid.uuid4(),
                                tenant_id=_uuid.UUID(tenant_id),
                                filename=doc["name"],
                                content_type=doc.get(
                                    "mime_type", "application/octet-stream"
                                ),
                                file_size=file_size,
                                storage_path=local_path,
                                status="uploaded",
                                chunk_count=0,
                            )
                            session.add(doc_record)
                            await session.commit()
                            await _process_document(str(doc_record.id), tenant_id)
                            logger.info("Ingested %s into RAG pipeline", doc["name"])
                    except Exception as exc:
                        logger.warning("Ingest failed for %s: %s", doc["name"], exc)

            except Exception as exc:
                logger.exception("Document sync+ingest background task failed: %s", exc)

    background_tasks.add_task(_ingest)

    return {
        "status": "started",
        "message": "Document sync and ingestion running in background",
    }
