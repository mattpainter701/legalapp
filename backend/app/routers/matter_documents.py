"""Router for matter file attachments (case documents)."""

import os
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter
from app.models.tenant import TenantSettings
from app.schemas.matter_document import (
    MatterDocumentListResponse,
    MatterDocumentResponse,
    MatterDocumentUpdate,
)
from app.services.matter_file_store import MatterFileStore
from app.services.matter_document_revisions import (
    DocumentRevisionServiceError,
    assert_assistant_derivative_category_preserved,
    assert_document_not_in_revision_lineage,
    assert_no_legacy_assistant_derivative_release,
)
from app.services.graph_client import graph_request
from app.services.google_client import google_request
from app.services.provider_http import (
    ProviderAuthError,
    ProviderError,
    ProviderNotFound,
    ProviderThrottled,
)
from app.services.token_vault import get_fresh_token

settings = get_settings()
router = APIRouter(prefix="/api", tags=["matter-documents"])
matter_file_store = MatterFileStore()


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


def _first_doc_attr(doc: MatterDocument, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = getattr(doc, name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _normalize_storage_provider(provider: str | None) -> str | None:
    if not provider:
        return None
    normalized = provider.lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "google": "google_drive",
        "gdrive": "google_drive",
        "drive": "google_drive",
        "microsoft_graph": "onedrive",
        "ms_graph": "onedrive",
        "msgraph": "onedrive",
        "one_drive": "onedrive",
        "local_disk": "local",
        "disk": "local",
        "filesystem": "local",
    }
    return aliases.get(normalized, normalized)


def _doc_storage_provider(doc: MatterDocument) -> str | None:
    return _normalize_storage_provider(
        _first_doc_attr(
            doc,
            (
                "storage_provider",
                "provider",
                "provider_backend",
                "cloud_provider",
                "storage_backend",
            ),
        )
    )


def _doc_provider_object_id(doc: MatterDocument) -> str | None:
    return _first_doc_attr(
        doc,
        (
            "provider_object_id",
            "provider_item_id",
            "cloud_object_id",
            "cloud_file_id",
        ),
    )


def _doc_provider_drive_id(doc: MatterDocument) -> str | None:
    return _first_doc_attr(doc, ("provider_drive_id", "drive_id", "cloud_drive_id"))


def _storage_result_document_fields(storage_result) -> dict:
    return {
        "storage_path": storage_result.storage_path,
        "storage_provider": storage_result.provider,
        "storage_backend": storage_result.backend,
        "provider_object_id": storage_result.provider_item_id,
        "provider_drive_id": storage_result.drive_id,
        "provider_parent_id": storage_result.parent_id,
        "storage_error": storage_result.error,
    }


def _cloud_token_provider(storage_provider: str) -> str:
    if storage_provider == "google_drive":
        return "google"
    return "microsoft"


async def _delete_cloud_provider_object(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    storage_provider: str,
    object_id: str,
    drive_id: str | None,
) -> None:
    """Router-local storage delete shim until this moves into a storage service."""
    token = await get_fresh_token(
        db, str(tenant_id), _cloud_token_provider(storage_provider)
    )
    if not token:
        raise ProviderAuthError(f"No connected token for {storage_provider}")

    safe_object_id = quote(object_id, safe="")
    safe_drive_id = quote(drive_id, safe="") if drive_id else None

    if storage_provider == "google_drive":
        await google_request(
            "DELETE",
            f"/{safe_object_id}",
            token=token,
            base_url="https://www.googleapis.com/drive/v3/files",
            provider_name="Google Drive",
        )
        return

    if storage_provider == "sharepoint":
        if not safe_drive_id:
            raise ProviderError("SharePoint document delete requires provider_drive_id")
        await graph_request(
            "DELETE",
            f"/drives/{safe_drive_id}/items/{safe_object_id}",
            token=token,
        )
        return

    if storage_provider == "onedrive":
        url = (
            f"/drives/{safe_drive_id}/items/{safe_object_id}"
            if safe_drive_id
            else f"/me/drive/items/{safe_object_id}"
        )
        await graph_request(
            "DELETE",
            url,
            token=token,
        )
        return

    raise ProviderError(f"Unsupported storage provider: {storage_provider}")


async def _delete_cloud_backing_if_needed(
    doc: MatterDocument,
    db: AsyncSession,
) -> None:
    storage_path = doc.storage_path or ""
    has_legacy_cloud_url = storage_path.startswith(("http://", "https://"))
    storage_provider = _doc_storage_provider(doc)
    object_id = _doc_provider_object_id(doc)

    if storage_provider in (None, "local") and not has_legacy_cloud_url:
        return

    if not storage_provider or storage_provider == "cloud" or not object_id:
        raise HTTPException(
            status_code=501,
            detail=(
                "Cloud-backed document deletion requires durable provider metadata; "
                "the database record was not removed."
            ),
        )

    try:
        await _delete_cloud_provider_object(
            db=db,
            tenant_id=doc.tenant_id,
            storage_provider=storage_provider,
            object_id=object_id,
            drive_id=_doc_provider_drive_id(doc),
        )
    except ProviderNotFound:
        return
    except ProviderThrottled as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Cloud provider throttled document deletion; "
                "database record was not removed."
            ),
            headers=(
                {"Retry-After": str(int(exc.retry_after))}
                if exc.retry_after is not None
                else None
            ),
        ) from exc
    except ProviderAuthError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Cloud provider credentials could not delete this document; "
                "database record was not removed."
            ),
        ) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Cloud provider document deletion failed; "
                "database record was not removed."
            ),
        ) from exc


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
    matter = await _get_matter_or_404(matter_id, user.tenant_id, db)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    file_bytes = await file.read()
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.MAX_FILE_SIZE_MB}MB",
        )

    # Load tenant cloud preference
    ts_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
    )
    ts = ts_result.scalar_one_or_none()
    preferred_provider = ts.primary_cloud_provider if ts else None

    doc_id = uuid.uuid4()
    safe_filename = os.path.basename(file.filename)
    storage_result = await matter_file_store.store_matter_file_result(
        db=db,
        tenant_id=str(user.tenant_id),
        matter_slug=matter.slug,
        category=document_category or "general",
        filename=safe_filename,
        content=file_bytes,
        content_type=file.content_type or "application/octet-stream",
        matter_cloud_folder=matter.cloud_folder,
        preferred_provider=preferred_provider,
    )

    doc = MatterDocument(
        id=doc_id,
        tenant_id=user.tenant_id,
        matter_id=uuid.UUID(matter_id),
        uploaded_by_user_id=user.id,
        filename=safe_filename,
        content_type=file.content_type,
        file_size=len(file_bytes),
        **_storage_result_document_fields(storage_result),
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
        try:
            await assert_assistant_derivative_category_preserved(
                db,
                tenant_id=user.tenant_id,
                matter_id=uuid.UUID(matter_id),
                document_id=doc.id,
                requested_category=body.document_category,
            )
        except DocumentRevisionServiceError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        doc.document_category = body.document_category
    if body.portal_visible is not None:
        if body.portal_visible:
            try:
                await assert_no_legacy_assistant_derivative_release(
                    db,
                    tenant_id=user.tenant_id,
                    matter_id=uuid.UUID(matter_id),
                    document_id=doc.id,
                )
            except DocumentRevisionServiceError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail={"code": exc.code, "message": exc.message},
                ) from exc
        doc.portal_visible = body.portal_visible

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
    await db.refresh(doc, with_for_update=True)

    try:
        await assert_document_not_in_revision_lineage(
            db,
            tenant_id=user.tenant_id,
            matter_id=uuid.UUID(matter_id),
            document_id=doc.id,
        )
    except DocumentRevisionServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    await _delete_cloud_backing_if_needed(doc, db)

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

    if doc.storage_path and doc.storage_path.startswith(("http://", "https://")):
        return RedirectResponse(doc.storage_path)

    if not doc.storage_path or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=doc.storage_path,
        filename=doc.filename,
        media_type=doc.content_type or "application/octet-stream",
    )
