"""Router for the matter document explorer: folders, filing, and tags.

Folders are per-matter and hierarchical; tags are firm-wide so one vocabulary
works across every matter. Both are tenant-scoped and every read re-derives the
tenant from the caller's session rather than trusting a path parameter.
"""

import uuid
import hashlib
import logging

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.matter_document import MatterDocument
from app.models.matter_document_tag import MatterDocumentTagLink
from app.models.plugin import Matter
from app.schemas.matter_document import (
    MatterDocumentFolderCreate,
    MatterDocumentFolderDeleteResponse,
    MatterDocumentFolderListResponse,
    MatterDocumentFolderResponse,
    MatterDocumentFolderUpdate,
    MatterDocumentMoveRequest,
    MatterDocumentMoveResponse,
    MatterDocumentTagAssignRequest,
    MatterDocumentTagCreate,
    MatterDocumentTagListResponse,
    MatterDocumentTagResponse,
    MatterDocumentTagUpdate,
)
from app.routers.matter_documents import (
    organization_http_error,
    serialize_documents,
)
from app.services.matter_document_organization import (
    DocumentOrganizationError,
    create_folder,
    create_tag,
    delete_folder,
    folder_document_counts,
    get_folder_or_404,
    get_tag_or_404,
    list_folders,
    list_tags,
    move_folder,
    rename_folder,
    set_document_tags,
    update_tag,
)

router = APIRouter(prefix="/api", tags=["matter-document-folders"])


async def _get_matter_or_404(
    matter_id: str, tenant_id: uuid.UUID, db: AsyncSession
) -> Matter:
    result = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


def _matter_uuid(matter_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(matter_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Matter not found") from exc


def _folder_response(folder, document_count: int) -> MatterDocumentFolderResponse:
    response = MatterDocumentFolderResponse.model_validate(folder)
    response.document_count = document_count
    return response


# ── Folders ──────────────────────────────────────────────────────────────────


@router.get(
    "/matters/{matter_id}/document-folders",
    response_model=MatterDocumentFolderListResponse,
)
async def list_matter_document_folders(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return the matter's whole folder tree with per-folder document counts."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)
    matter_uuid = _matter_uuid(matter_id)

    folders = await list_folders(db, tenant_id=user.tenant_id, matter_id=matter_uuid)
    counts = await folder_document_counts(
        db, tenant_id=user.tenant_id, matter_id=matter_uuid
    )
    return MatterDocumentFolderListResponse(
        items=[_folder_response(f, counts.get(f.id, 0)) for f in folders],
        total=len(folders),
        root_document_count=counts.get(None, 0),
    )


@router.post(
    "/matters/{matter_id}/document-folders",
    response_model=MatterDocumentFolderResponse,
    status_code=201,
)
async def create_matter_document_folder(
    matter_id: str,
    body: MatterDocumentFolderCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)

    try:
        folder = await create_folder(
            db,
            tenant_id=user.tenant_id,
            matter_id=_matter_uuid(matter_id),
            name=body.name,
            parent_id=body.parent_id,
            created_by_user_id=user.id,
        )
    except DocumentOrganizationError as exc:
        raise organization_http_error(exc) from exc

    await db.commit()
    await db.refresh(folder)
    return _folder_response(folder, 0)


@router.patch(
    "/matters/{matter_id}/document-folders/{folder_id}",
    response_model=MatterDocumentFolderResponse,
)
async def update_matter_document_folder(
    matter_id: str,
    folder_id: uuid.UUID,
    body: MatterDocumentFolderUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Rename and/or reparent a folder.

    ``parent_id`` is only applied when the client actually sent the field, so
    a rename-only request cannot accidentally move the folder to the root.
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)
    matter_uuid = _matter_uuid(matter_id)

    try:
        folder = await get_folder_or_404(
            db,
            tenant_id=user.tenant_id,
            matter_id=matter_uuid,
            folder_id=folder_id,
        )
        if body.name is not None:
            folder = await rename_folder(db, folder, name=body.name)
        if "parent_id" in body.model_fields_set:
            folder = await move_folder(db, folder, new_parent_id=body.parent_id)
    except DocumentOrganizationError as exc:
        raise organization_http_error(exc) from exc

    await db.commit()
    await db.refresh(folder)
    counts = await folder_document_counts(
        db, tenant_id=user.tenant_id, matter_id=matter_uuid
    )
    return _folder_response(folder, counts.get(folder.id, 0))


@router.delete(
    "/matters/{matter_id}/document-folders/{folder_id}",
    response_model=MatterDocumentFolderDeleteResponse,
)
async def delete_matter_document_folder(
    matter_id: str,
    folder_id: uuid.UUID,
    request: Request,
    move_documents_to_parent: bool = Query(
        False,
        description=(
            "Re-file the subtree's documents into this folder's parent instead "
            "of refusing to delete a folder that still holds documents."
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """Delete a folder subtree. Documents are re-filed, never deleted."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)

    try:
        folder = await get_folder_or_404(
            db,
            tenant_id=user.tenant_id,
            matter_id=_matter_uuid(matter_id),
            folder_id=folder_id,
        )
        parent_id = folder.parent_id
        documents_moved = await delete_folder(
            db, folder, move_documents_to_parent=move_documents_to_parent
        )
    except DocumentOrganizationError as exc:
        raise organization_http_error(exc) from exc

    await db.commit()
    return MatterDocumentFolderDeleteResponse(
        deleted_folder_id=folder_id,
        documents_moved=documents_moved,
        moved_to_folder_id=parent_id if documents_moved else None,
    )


# ── Filing documents ─────────────────────────────────────────────────────────


@router.post(
    "/matters/{matter_id}/documents/move",
    response_model=MatterDocumentMoveResponse,
)
async def move_matter_documents(
    matter_id: str,
    body: MatterDocumentMoveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """File one or more documents into a folder, or back to the matter root.

    This changes where the document appears in the explorer. A copy already
    written to the firm's cloud share stays at the path it was uploaded to;
    only newly uploaded files are written to the mirrored folder path.
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)
    matter_uuid = _matter_uuid(matter_id)

    if body.folder_id is not None:
        try:
            await get_folder_or_404(
                db,
                tenant_id=user.tenant_id,
                matter_id=matter_uuid,
                folder_id=body.folder_id,
            )
        except DocumentOrganizationError as exc:
            raise organization_http_error(exc) from exc

    requested_ids = list(dict.fromkeys(body.document_ids))
    result = await db.execute(
        select(MatterDocument).where(
            MatterDocument.tenant_id == user.tenant_id,
            MatterDocument.matter_id == matter_uuid,
            MatterDocument.id.in_(requested_ids),
        )
    )
    documents = list(result.scalars().all())
    if len(documents) != len(requested_ids):
        raise HTTPException(
            status_code=404,
            detail="One or more documents were not found in this matter",
        )

    for document in documents:
        document.folder_id = body.folder_id
    await db.commit()

    for document in documents:
        await db.refresh(document)
    return MatterDocumentMoveResponse(
        moved=len(documents),
        folder_id=body.folder_id,
        items=await serialize_documents(
            db, tenant_id=user.tenant_id, documents=documents
        ),
    )


class DocumentCopyRequest(BaseModel):
    document_id: uuid.UUID
    folder_id: uuid.UUID | None = None
    copy_id: uuid.UUID


@router.post("/matters/{matter_id}/documents/copy", status_code=201)
async def copy_matter_document(
    matter_id: str,
    body: DocumentCopyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Copy bytes to a new private document; never change or delete the source.

    The caller retains copy_id across retries. Names include that ID so a copy
    cannot overwrite a same-named file in a destination cloud folder.
    """
    from app.database import async_session_maker
    from app.routers.matter_documents import (
        _storage_result_document_fields,
        serialize_document,
    )
    from app.services.matter_document_organization import storage_routing_for_folder
    from app.services.matter_document_revisions import (
        assert_no_legacy_assistant_derivative_release,
        DocumentRevisionServiceError,
    )
    from app.services.matter_file_store import MatterFileStore
    from app.config import get_settings

    user = await get_current_user(request, db)
    tenant_id, user_id, matter_uuid = user.tenant_id, user.id, _matter_uuid(matter_id)
    await set_tenant_context(db, str(tenant_id))
    matter = await _get_matter_or_404(matter_id, tenant_id, db)
    # Serialize copy retries on the matter row, away from storage OAuth commits.
    await db.scalar(
        select(Matter.id)
        .where(Matter.id == matter_uuid, Matter.tenant_id == tenant_id)
        .with_for_update()
    )
    folder = None
    if body.folder_id:
        try:
            folder = await get_folder_or_404(
                db, tenant_id=tenant_id, matter_id=matter_uuid, folder_id=body.folder_id
            )
        except DocumentOrganizationError as exc:
            raise organization_http_error(exc) from exc
    source = await db.scalar(
        select(MatterDocument).where(
            MatterDocument.id == body.document_id,
            MatterDocument.tenant_id == tenant_id,
            MatterDocument.matter_id == matter_uuid,
        )
    )
    if source is None:
        raise HTTPException(404, "Source document not found")
    try:
        await assert_no_legacy_assistant_derivative_release(
            db, tenant_id=tenant_id, matter_id=matter_uuid, document_id=source.id
        )
    except DocumentRevisionServiceError as exc:
        raise HTTPException(
            409, "Use the document's release workflow to create a copy"
        ) from exc
    # Persist source identity in a human-readable description for retry validation.
    provenance = f"Copy of document {source.id}"
    existing = await db.scalar(
        select(MatterDocument).where(
            MatterDocument.id == body.copy_id, MatterDocument.tenant_id == tenant_id
        )
    )
    if existing:
        if (
            existing.matter_id != matter_uuid
            or existing.folder_id != body.folder_id
            or existing.description != provenance
        ):
            raise HTTPException(
                409, "This copy request ID was already used for another destination"
            )
        return await serialize_document(db, tenant_id=tenant_id, document=existing)
    store = MatterFileStore()
    category, segments = storage_routing_for_folder(folder)
    filename = f"Copy-{body.copy_id.hex[:12]}-{source.filename}"[:500]
    async with async_session_maker() as storage_db:
        await set_tenant_context(storage_db, str(tenant_id))
        content = await store.read_matter_file_bytes(
            db=storage_db,
            tenant_id=str(tenant_id),
            document=source,
            max_bytes=get_settings().MAX_FILE_SIZE_MB * 1024 * 1024,
        )
        stored = await store.store_matter_file_result(
            db=storage_db,
            tenant_id=str(tenant_id),
            matter_slug=matter.slug,
            category=category or source.document_category or "general",
            filename=filename,
            content=content,
            content_type=source.content_type or "application/octet-stream",
            matter_cloud_folder=matter.cloud_folder,
            folder_path=segments,
        )
    if not stored.succeeded:
        raise HTTPException(502, "The destination could not save this copy")
    copied = MatterDocument(
        id=body.copy_id,
        tenant_id=tenant_id,
        matter_id=matter_uuid,
        uploaded_by_user_id=user_id,
        folder_id=body.folder_id,
        filename=filename,
        content_type=source.content_type,
        file_size=len(content),
        document_category=source.document_category,
        description=provenance,
        portal_visible=False,
        document_sha256=hashlib.sha256(content).hexdigest(),
        positioned_fields=source.positioned_fields,
        signing_roles=source.signing_roles,
        signing_placement_required=source.signing_placement_required,
        **_storage_result_document_fields(stored),
    )
    db.add(copied)
    try:
        await db.commit()
    except Exception:
        # A lost commit acknowledgement is ambiguous: deleting here could erase
        # a committed copy. Retain its unique storage object for reconciliation.
        logging.getLogger(__name__).exception(
            "Copy persistence uncertain: tenant=%s copy=%s storage=%s",
            tenant_id,
            body.copy_id,
            stored.storage_path,
        )
        raise HTTPException(
            503, "Copy persistence is uncertain. Retry the same copy request."
        )
    await db.refresh(copied)
    return await serialize_document(db, tenant_id=tenant_id, document=copied)


# ── Tags ─────────────────────────────────────────────────────────────────────


@router.get("/document-tags", response_model=MatterDocumentTagListResponse)
async def list_document_tags(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List the firm's document tag vocabulary."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    tags = await list_tags(db, tenant_id=user.tenant_id)
    return MatterDocumentTagListResponse(
        items=[MatterDocumentTagResponse.model_validate(t) for t in tags],
        total=len(tags),
    )


@router.post(
    "/document-tags", response_model=MatterDocumentTagResponse, status_code=201
)
async def create_document_tag(
    body: MatterDocumentTagCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    try:
        tag = await create_tag(
            db,
            tenant_id=user.tenant_id,
            name=body.name,
            color=body.color,
            created_by_user_id=user.id,
        )
    except DocumentOrganizationError as exc:
        raise organization_http_error(exc) from exc
    await db.commit()
    await db.refresh(tag)
    return MatterDocumentTagResponse.model_validate(tag)


@router.patch("/document-tags/{tag_id}", response_model=MatterDocumentTagResponse)
async def update_document_tag(
    tag_id: uuid.UUID,
    body: MatterDocumentTagUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    try:
        tag = await get_tag_or_404(db, tenant_id=user.tenant_id, tag_id=tag_id)
        tag = await update_tag(db, tag, name=body.name, color=body.color)
    except DocumentOrganizationError as exc:
        raise organization_http_error(exc) from exc
    await db.commit()
    await db.refresh(tag)
    return MatterDocumentTagResponse.model_validate(tag)


@router.delete("/document-tags/{tag_id}", status_code=204)
async def delete_document_tag(
    tag_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a tag firm-wide; its assignments go with it, documents do not."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    try:
        tag = await get_tag_or_404(db, tenant_id=user.tenant_id, tag_id=tag_id)
    except DocumentOrganizationError as exc:
        raise organization_http_error(exc) from exc
    await db.execute(
        MatterDocumentTagLink.__table__.delete().where(
            MatterDocumentTagLink.tenant_id == user.tenant_id,
            MatterDocumentTagLink.tag_id == tag.id,
        )
    )
    await db.delete(tag)
    await db.commit()


@router.put(
    "/matters/{matter_id}/documents/{doc_id}/tags",
    response_model=MatterDocumentTagListResponse,
)
async def set_matter_document_tags(
    matter_id: str,
    doc_id: uuid.UUID,
    body: MatterDocumentTagAssignRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Replace a document's tags with exactly the supplied set."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _get_matter_or_404(matter_id, user.tenant_id, db)

    result = await db.execute(
        select(MatterDocument).where(
            MatterDocument.id == doc_id,
            MatterDocument.matter_id == _matter_uuid(matter_id),
            MatterDocument.tenant_id == user.tenant_id,
        )
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        tags = await set_document_tags(
            db,
            tenant_id=user.tenant_id,
            document=document,
            tag_ids=body.tag_ids,
            actor_user_id=user.id,
        )
    except DocumentOrganizationError as exc:
        raise organization_http_error(exc) from exc

    await db.commit()
    return MatterDocumentTagListResponse(
        items=[MatterDocumentTagResponse.model_validate(t) for t in tags],
        total=len(tags),
    )
