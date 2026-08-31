"""Revision-safe REST surface for the Template Studio server foundation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.studio_draft import (
    StudioDraftCreate,
    StudioDraftImport,
    StudioDraftPatch,
    StudioDraftResponse,
    StudioPromoteRequest,
    StudioRevisionRequest,
    StudioSnapshotResponse,
    StudioSourceContract,
    StudioValidationResponse,
)
from app.services.access_control import require_capability
from app.services.studio_drafts import StudioDraftService, StudioError

router = APIRouter(prefix="/api/template-studio/drafts", tags=["template-studio"])


def _service(db: AsyncSession, user) -> StudioDraftService:
    return StudioDraftService(db, user.tenant_id, user.id)


async def _result(awaitable):
    try:
        return await awaitable
    except StudioError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _set_etag(response: Response, payload: dict) -> dict:
    if payload.get("etag"):
        response.headers["ETag"] = payload["etag"]
    response.headers["Cache-Control"] = "private, no-store"
    return payload


@router.post("", response_model=StudioDraftResponse, status_code=201)
async def create_studio_draft(
    body: StudioDraftCreate,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    payload = await _result(_service(db, current_user).create(body, idempotency_key))
    return _set_etag(response, payload)


@router.post("/imports", response_model=StudioDraftResponse, status_code=201)
async def import_published_template(
    body: StudioDraftImport,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    payload = await _result(
        _service(db, current_user).import_template(body, idempotency_key)
    )
    return _set_etag(response, payload)


@router.get("/{draft_id}", response_model=StudioDraftResponse)
async def read_studio_draft(
    draft_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    payload = await _result(_service(db, current_user).read(draft_id))
    return _set_etag(response, payload)


@router.post("/{draft_id}/resume", response_model=StudioDraftResponse)
async def resume_studio_draft(
    draft_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    payload = await _result(_service(db, current_user).read(draft_id))
    return _set_etag(response, payload)


@router.patch("/{draft_id}", response_model=StudioDraftResponse)
async def patch_studio_draft(
    draft_id: uuid.UUID,
    body: StudioDraftPatch,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    payload = await _result(
        _service(db, current_user).patch(draft_id, body, idempotency_key)
    )
    return _set_etag(response, payload)


@router.post("/{draft_id}/validate", response_model=StudioValidationResponse)
async def validate_studio_draft(
    draft_id: uuid.UUID,
    body: StudioRevisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    return await _result(_service(db, current_user).validate(draft_id, body))


@router.post(
    "/{draft_id}/snapshots", response_model=StudioSnapshotResponse, status_code=201
)
async def snapshot_studio_draft(
    draft_id: uuid.UUID,
    body: StudioRevisionRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    return await _result(
        _service(db, current_user).snapshot(draft_id, body, idempotency_key)
    )


@router.get(
    "/{draft_id}/snapshots/{snapshot_id}", response_model=StudioSnapshotResponse
)
async def read_studio_snapshot(
    draft_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    return await _result(
        _service(db, current_user).read_snapshot(draft_id, snapshot_id)
    )


@router.get("/{draft_id}/source-contract", response_model=StudioSourceContract)
async def read_worker_source_contract(
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    payload = await _result(_service(db, current_user).read(draft_id))
    return payload["source"]


@router.post("/{draft_id}/promote", response_model=StudioDraftResponse)
async def promote_studio_draft(
    draft_id: uuid.UUID,
    body: StudioPromoteRequest,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_capability("manage_documents")),
):
    payload = await _result(
        _service(db, current_user).promote(draft_id, body, idempotency_key)
    )
    return _set_etag(response, payload)
