"""Administrator-controlled cloud-provider migration endpoints."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import (
    async_session_maker,
    bind_tenant_context,
    get_db,
    set_tenant_context,
)
from app.middleware.tenant import require_admin
from app.models.storage_migration import StorageMigration, StorageMigrationMatch
from app.services.storage_migration import StorageMigrationService
from app.services.storage_migration_reindex import storage_migration_reindex

router = APIRouter(prefix="/api/admin/storage-migrations", tags=["storage-migration"])
service = StorageMigrationService()


class MigrationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_provider: str
    target_root_id: str | None = Field(default=None, max_length=512)
    target_drive_id: str | None = Field(default=None, max_length=512)


class MigrationReconcile(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MigrationCutover(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_version: str = Field(min_length=1, max_length=200)
    acknowledged_policy: str = Field(pattern="^all-matters-and-documents-resolved$")


def _state(row):
    return {
        "id": str(row.id),
        "phase": row.phase,
        "source_provider": row.source_provider,
        "target_provider": row.target_provider,
        "bucket_counts": row.bucket_counts or {},
        "evidence_version": row.evidence_version,
        "needs_reindex": bool(row.needs_reindex),
        "error_message": row.error_message,
        "acknowledged_policy": row.acknowledged_policy,
    }


async def _user_tenant(request, db):
    user = await require_admin(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    return user


async def _owned(db, migration_id, user):
    row = (
        await db.execute(
            select(StorageMigration).where(
                StorageMigration.id == migration_id,
                StorageMigration.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Migration not found")
    return row


async def _reindex(tenant_id):
    # A separate session outlives the request. The persisted flag also lets the
    # scheduler retry if this process exits before its background task runs.
    async with async_session_maker() as db:
        await bind_tenant_context(db, tenant_id)
        await storage_migration_reindex.run(db, tenant_id)


@router.post("")
async def start_migration(
    body: MigrationStart, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _user_tenant(request, db)
    root = None
    if body.target_root_id:
        root = {"id": body.target_root_id, "path": "claritylegal-records"}
        if body.target_drive_id:
            root["drive_id"] = body.target_drive_id
    elif body.target_drive_id:
        raise HTTPException(
            status_code=422, detail="A drive ID requires a target root folder ID"
        )
    try:
        row = await service.start(
            db,
            str(user.tenant_id),
            body.target_provider,
            str(user.id),
            target_root=root,
        )
        await db.commit()
        return _state(row)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/latest")
async def latest_migration(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _user_tenant(request, db)
    row = (
        await db.execute(
            select(StorageMigration)
            .where(
                StorageMigration.tenant_id == user.tenant_id,
            )
            .order_by(StorageMigration.started_at.desc(), StorageMigration.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return _state(row) if row else None


@router.get("/{migration_id}")
async def get_migration(
    migration_id: UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _user_tenant(request, db)
    return _state(await _owned(db, migration_id, user))


@router.get("/{migration_id}/matches")
async def list_migration_matches(
    migration_id: UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _user_tenant(request, db)
    row = await _owned(db, migration_id, user)
    matches = (
        (
            await db.execute(
                select(StorageMigrationMatch)
                .where(
                    StorageMigrationMatch.migration_id == row.id,
                    StorageMigrationMatch.tenant_id == user.tenant_id,
                )
                .order_by(
                    StorageMigrationMatch.object_type, StorageMigrationMatch.object_id
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "object_type": m.object_type,
            "object_id": m.object_id,
            "bucket": m.bucket,
            "matching_rung": m.matching_rung,
            "evidence": m.evidence,
        }
        for m in matches
    ]


@router.post("/{migration_id}/reconcile")
async def reconcile_migration(
    migration_id: UUID,
    body: MigrationReconcile,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _user_tenant(request, db)
    await _owned(db, migration_id, user)
    try:
        row = await service.reconcile(db, str(migration_id))
        await db.commit()
        return _state(row)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{migration_id}/cutover")
async def cutover_migration(
    migration_id: UUID,
    body: MigrationCutover,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _user_tenant(request, db)
    await _owned(db, migration_id, user)
    try:
        row = await service.cutover(
            db,
            str(migration_id),
            operator_id=str(user.id),
            evidence_version=body.evidence_version,
            acknowledged_policy=body.acknowledged_policy,
        )
        await db.commit()
        result = _state(row)
        background_tasks.add_task(_reindex, str(user.tenant_id))
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{migration_id}/reindex")
async def retry_reindex(
    migration_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    user = await _user_tenant(request, db)
    row = await _owned(db, migration_id, user)
    if row.phase != "complete" or not row.needs_reindex:
        raise HTTPException(
            status_code=409, detail="This migration has no pending reindex"
        )
    background_tasks.add_task(_reindex, str(user.tenant_id))
    return _state(row)


@router.post("/{migration_id}/abandon")
async def abandon_migration(
    migration_id: UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _user_tenant(request, db)
    await _owned(db, migration_id, user)
    try:
        row = await service.abandon(db, str(migration_id), str(user.id))
        await db.commit()
        return _state(row)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
