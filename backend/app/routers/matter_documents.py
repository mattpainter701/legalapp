"""Router for matter file attachments (case documents)."""

import os
import uuid
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.upload_guard import reject_oversized_request
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.matter_document import MatterDocument
from app.models.matter_document_folder import MatterDocumentFolder
from app.models.matter_document_tag import MatterDocumentTagLink
from app.models.plugin import Matter
from app.models.tenant import TenantSettings
from app.schemas.matter_document import (
    MatterDocumentListResponse,
    MatterDocumentResponse,
    MatterDocumentTagResponse,
    MatterDocumentUpdate,
)
from app.services.document_accountability import append_document_integrity_event
from app.services.matter_document_organization import (
    DocumentOrganizationError,
    get_folder_or_404,
    storage_routing_for_folder,
    tags_for_documents,
)
from app.services.matter_file_store import (
    MatterFileIntegrityError,
    MatterFileNotFound,
    MatterFileReadError,
    MatterFileStore,
)
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


def organization_http_error(exc: DocumentOrganizationError) -> HTTPException:
    """Translate a folder/tag service error into the router's error shape."""
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


async def serialize_documents(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    documents: list[MatterDocument],
) -> list[MatterDocumentResponse]:
    """Attach folder paths and tags without an N+1 query per document."""
    if not documents:
        return []

    folder_ids = {doc.folder_id for doc in documents if doc.folder_id}
    folder_paths: dict[uuid.UUID, str] = {}
    if folder_ids:
        result = await db.execute(
            select(MatterDocumentFolder.id, MatterDocumentFolder.path).where(
                MatterDocumentFolder.tenant_id == tenant_id,
                MatterDocumentFolder.id.in_(folder_ids),
            )
        )
        folder_paths = {row[0]: row[1] for row in result.all()}

    tags_by_document = await tags_for_documents(
        db, tenant_id=tenant_id, document_ids=[doc.id for doc in documents]
    )

    responses = []
    for doc in documents:
        response = MatterDocumentResponse.model_validate(doc)
        response.folder_path = (
            folder_paths.get(doc.folder_id) if doc.folder_id else None
        )
        response.tags = [
            MatterDocumentTagResponse.model_validate(tag)
            for tag in tags_by_document.get(doc.id, [])
        ]
        responses.append(response)
    return responses


async def serialize_document(
    db: AsyncSession, *, tenant_id: uuid.UUID, document: MatterDocument
) -> MatterDocumentResponse:
    return (await serialize_documents(db, tenant_id=tenant_id, documents=[document]))[0]


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
    folder_id: str | None = Query(
        None,
        description=(
            "Filter to one folder. Omit for every document in the matter; pass "
            "'root' for documents that are not filed in any folder."
        ),
    ),
    include_subfolders: bool = Query(False),
    q: str | None = Query(None, max_length=200),
    tag_ids: list[uuid.UUID] = Query(default_factory=list),
    sort: str = Query("created_at", pattern="^(created_at|filename|file_size)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
):
    """List documents attached to a matter, optionally scoped to a folder."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)

    stmt = select(MatterDocument).where(
        MatterDocument.matter_id == matter_id,
        MatterDocument.tenant_id == user.tenant_id,
    )

    requested_folder = (folder_id or "").strip()
    if requested_folder.lower() == "root":
        stmt = stmt.where(MatterDocument.folder_id.is_(None))
    elif requested_folder:
        try:
            folder_uuid = uuid.UUID(requested_folder)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="folder_id must be a UUID or 'root'"
            ) from exc
        try:
            folder = await get_folder_or_404(
                db,
                tenant_id=user.tenant_id,
                matter_id=uuid.UUID(matter_id),
                folder_id=folder_uuid,
            )
        except DocumentOrganizationError as exc:
            raise organization_http_error(exc) from exc
        if include_subfolders:
            subtree = select(MatterDocumentFolder.id).where(
                MatterDocumentFolder.tenant_id == user.tenant_id,
                MatterDocumentFolder.matter_id == folder.matter_id,
                or_(
                    MatterDocumentFolder.id == folder.id,
                    MatterDocumentFolder.path.startswith(f"{folder.path}/"),
                ),
            )
            stmt = stmt.where(MatterDocument.folder_id.in_(subtree))
        else:
            stmt = stmt.where(MatterDocument.folder_id == folder.id)

    search = (q or "").strip()
    if search:
        # ``\`` is the default LIKE escape in Postgres, so neutralize it first.
        pattern = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            or_(
                MatterDocument.filename.ilike(f"%{pattern}%"),
                MatterDocument.description.ilike(f"%{pattern}%"),
            )
        )

    for tag_id in dict.fromkeys(tag_ids):
        # One EXISTS per tag makes the filter conjunctive: a document must
        # carry every requested tag, not merely one of them.
        stmt = stmt.where(
            select(MatterDocumentTagLink.tag_id)
            .where(
                MatterDocumentTagLink.tenant_id == user.tenant_id,
                MatterDocumentTagLink.document_id == MatterDocument.id,
                MatterDocumentTagLink.tag_id == tag_id,
            )
            .exists()
        )

    sort_columns = {
        "created_at": MatterDocument.created_at,
        "filename": func.lower(MatterDocument.filename),
        "file_size": MatterDocument.file_size,
    }
    sort_column = sort_columns[sort]
    stmt = stmt.order_by(
        sort_column.asc() if order == "asc" else sort_column.desc(),
        # A stable tiebreaker keeps paging and test assertions deterministic
        # when several documents share a name, size, or timestamp.
        MatterDocument.id.asc(),
    )

    result = await db.execute(stmt)
    docs = list(result.scalars().all())
    return MatterDocumentListResponse(
        items=await serialize_documents(db, tenant_id=user.tenant_id, documents=docs),
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
    folder_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file attachment to a matter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    matter = await _get_matter_or_404(matter_id, user.tenant_id, db)

    folder: MatterDocumentFolder | None = None
    if (folder_id or "").strip():
        try:
            folder = await get_folder_or_404(
                db,
                tenant_id=user.tenant_id,
                matter_id=uuid.UUID(matter_id),
                folder_id=uuid.UUID(folder_id.strip()),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="folder_id must be a UUID"
            ) from exc
        except DocumentOrganizationError as exc:
            raise organization_http_error(exc) from exc

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    reject_oversized_request(request, max_bytes, settings.MAX_FILE_SIZE_MB)
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
    category_override, folder_segments = storage_routing_for_folder(folder)
    storage_result = await matter_file_store.store_matter_file_result(
        db=db,
        tenant_id=str(user.tenant_id),
        matter_slug=matter.slug,
        category=category_override or document_category or "general",
        filename=safe_filename,
        content=file_bytes,
        content_type=file.content_type or "application/octet-stream",
        matter_cloud_folder=matter.cloud_folder,
        preferred_provider=preferred_provider,
        folder_path=folder_segments,
    )

    doc = MatterDocument(
        id=doc_id,
        tenant_id=user.tenant_id,
        matter_id=uuid.UUID(matter_id),
        uploaded_by_user_id=user.id,
        folder_id=folder.id if folder else None,
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
    return await serialize_document(db, tenant_id=user.tenant_id, document=doc)


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
    return await serialize_document(db, tenant_id=user.tenant_id, document=doc)


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


@router.get("/matters/{matter_id}/documents/{doc_id}/open")
async def open_matter_document(
    matter_id: str,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Resolve a fresh tenant-provider editing URL from durable object IDs."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    doc = await _get_doc_or_404(doc_id, matter_id, user.tenant_id, db)
    try:
        url = await matter_file_store.get_matter_file_open_url(
            db=db,
            tenant_id=str(user.tenant_id),
            document=doc,
        )
    except MatterFileNotFound as exc:
        raise HTTPException(status_code=404, detail="Cloud document not found") from exc
    except (MatterFileReadError, ProviderError) as exc:
        raise HTTPException(
            status_code=409,
            detail="The cloud document cannot be opened until its binding is repaired",
        ) from exc
    return RedirectResponse(
        url,
        status_code=307,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/matters/{matter_id}/documents/{doc_id}/download")
async def download_matter_document(
    matter_id: str,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Download exact registered bytes without trusting a persisted display URL."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    doc = await _get_doc_or_404(doc_id, matter_id, user.tenant_id, db)

    if doc.storage_backend in {"onedrive", "sharepoint", "google_drive"}:
        if doc.storage_state == "conflict":
            raise HTTPException(
                status_code=409,
                detail="This cloud document must be reconciled before download",
            )
        expected_sha256 = (
            doc.document_sha256 if doc.storage_state == "verified" else None
        )
        try:
            content = await matter_file_store.read_matter_file_bytes(
                db=db,
                tenant_id=str(user.tenant_id),
                document=doc,
                expected_sha256=expected_sha256,
                expected_size=doc.file_size,
            )
        except MatterFileIntegrityError as exc:
            doc.storage_state = "conflict"
            doc.storage_error = (
                "Download verification failed; cloud reconciliation is required"
            )
            if doc.generated_artifact_id and doc.generated_artifact_revision_id:
                await append_document_integrity_event(
                    db,
                    tenant_id=user.tenant_id,
                    matter_id=doc.matter_id,
                    task_id=doc.task_id,
                    artifact_id=doc.generated_artifact_id,
                    artifact_revision_id=doc.generated_artifact_revision_id,
                    document_id=doc.id,
                    event_type="cloud_download_integrity_conflict",
                    actor_type="user",
                    actor_user_id=user.id,
                    content_sha256=doc.document_sha256,
                    provider_object_id=doc.provider_object_id,
                    provider_etag=doc.provider_etag,
                    provider_version_id=doc.provider_version_id,
                    metadata={"storage_backend": doc.storage_backend},
                )
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail="The cloud document changed and must be reconciled",
            ) from exc
        except MatterFileNotFound as exc:
            raise HTTPException(
                status_code=404, detail="Cloud document not found"
            ) from exc
        except (MatterFileReadError, ProviderError) as exc:
            raise HTTPException(
                status_code=503,
                detail="The cloud document is temporarily unavailable",
            ) from exc
        return Response(
            content=content,
            media_type=doc.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": (
                    "attachment; filename*=UTF-8''" f"{quote(doc.filename, safe='')}"
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    if not doc.storage_path or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=doc.storage_path,
        filename=doc.filename,
        media_type=doc.content_type or "application/octet-stream",
    )
