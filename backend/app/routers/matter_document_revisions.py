"""Tenant-scoped API for review-first DOCX revision derivatives."""

from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.schemas.matter_document_revision import (
    MatterDocumentRevisionApprove,
    MatterDocumentRevisionCreate,
    MatterDocumentRevisionListResponse,
    MatterDocumentRevisionReject,
    MatterDocumentRevisionResponse,
    SignatureReplacementPrepare,
)
from app.services.access_control import require_capability
from app.services.matter_document_revisions import (
    DocumentRevisionServiceError,
    matter_document_revision_service,
)

router = APIRouter(prefix="/api/matters", tags=["matter-document-revisions"])


def _http_error(exc: DocumentRevisionServiceError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.post(
    "/{matter_id}/documents/{source_document_id}/revisions",
    response_model=MatterDocumentRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_revision(
    matter_id: UUID,
    source_document_id: UUID,
    body: MatterDocumentRevisionCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    try:
        return await matter_document_revision_service.create_revision(
            db, current_user, matter_id, source_document_id, body
        )
    except DocumentRevisionServiceError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/{matter_id}/document-revisions/{revision_id}",
    response_model=MatterDocumentRevisionResponse,
)
async def get_document_revision(
    matter_id: UUID,
    revision_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    try:
        return await matter_document_revision_service.get_revision(
            db, current_user.tenant_id, matter_id, revision_id
        )
    except DocumentRevisionServiceError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/{matter_id}/document-revisions/{revision_id}/artifact",
    response_class=Response,
)
async def download_document_revision_artifact(
    matter_id: UUID,
    revision_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    try:
        content, document = await matter_document_revision_service.artifact(
            db, current_user.tenant_id, matter_id, revision_id
        )
    except DocumentRevisionServiceError as exc:
        raise _http_error(exc) from exc
    safe_filename = quote(document.filename, safe="")
    return Response(
        content=content,
        media_type=document.content_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/{matter_id}/documents/{root_document_id}/revisions",
    response_model=MatterDocumentRevisionListResponse,
)
async def list_document_revisions(
    matter_id: UUID,
    root_document_id: UUID,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    try:
        return await matter_document_revision_service.list_revisions(
            db,
            current_user.tenant_id,
            matter_id,
            root_document_id,
            limit=limit,
            offset=offset,
        )
    except DocumentRevisionServiceError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{matter_id}/document-revisions/{revision_id}/approve",
    response_model=MatterDocumentRevisionResponse,
)
async def approve_document_revision(
    matter_id: UUID,
    revision_id: UUID,
    body: MatterDocumentRevisionApprove,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    try:
        return await matter_document_revision_service.approve(
            db, current_user, matter_id, revision_id, body
        )
    except DocumentRevisionServiceError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{matter_id}/document-revisions/{revision_id}/reject",
    response_model=MatterDocumentRevisionResponse,
)
async def reject_document_revision(
    matter_id: UUID,
    revision_id: UUID,
    body: MatterDocumentRevisionReject,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    try:
        return await matter_document_revision_service.reject(
            db, current_user, matter_id, revision_id, body
        )
    except DocumentRevisionServiceError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{matter_id}/document-revisions/{revision_id}/prepare-esign-replacement",
    response_model=MatterDocumentRevisionResponse,
)
async def prepare_document_revision_esign_replacement(
    matter_id: UUID,
    revision_id: UUID,
    body: SignatureReplacementPrepare,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    try:
        return await matter_document_revision_service.prepare_esign_replacement(
            db, current_user, matter_id, revision_id, body
        )
    except DocumentRevisionServiceError as exc:
        raise _http_error(exc) from exc
