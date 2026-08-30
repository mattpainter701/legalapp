"""Collaborative, review-first research workspaces.

This surface stores attorney work product; it does not retrieve authorities or
claim citation correctness.  Source and evidence labels are retained verbatim
through records, snapshots, and export.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.plugin import Matter
from app.models.matter_assignment import MatterAssignment
from app.models.research_workspace import (
    ResearchRecord,
    ResearchRecordRevision,
    ResearchWorkspace,
    ResearchWorkspaceEvent,
    ResearchWorkspaceIdempotency,
    ResearchWorkspaceMember,
    ResearchWorkspaceSnapshot,
)
from app.models.user import User
from app.schemas.research_workspace import (
    MemberUpsert,
    RecordCreate,
    RecordUpdate,
    SnapshotCreate,
    WorkspaceCreate,
)

router = APIRouter(
    prefix="/api/matters/{matter_id}/research-workspaces", tags=["research-workspaces"]
)
_WRITE_ROLES = {"owner", "editor", "reviewer"}


async def _matter(matter_id: uuid.UUID, user, db: AsyncSession) -> Matter:
    row = await db.execute(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == user.tenant_id)
    )
    matter = row.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    if user.role != "admin" and matter.user_id != user.id:
        assigned = await db.execute(
            select(MatterAssignment.id).where(
                MatterAssignment.tenant_id == user.tenant_id,
                MatterAssignment.matter_id == matter_id,
                MatterAssignment.user_id == user.id,
                MatterAssignment.is_active_working.is_(True),
            )
        )
        if assigned.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=403, detail="You are not assigned to that matter"
            )
    return matter


async def _workspace(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user,
    db: AsyncSession,
    *,
    role: str | None = None,
    lock: bool = False,
) -> tuple[ResearchWorkspace, ResearchWorkspaceMember]:
    await _matter(matter_id, user, db)
    query = (
        select(ResearchWorkspace, ResearchWorkspaceMember)
        .join(
            ResearchWorkspaceMember,
            ResearchWorkspaceMember.workspace_id == ResearchWorkspace.id,
        )
        .where(
            ResearchWorkspace.id == workspace_id,
            ResearchWorkspace.matter_id == matter_id,
            ResearchWorkspace.tenant_id == user.tenant_id,
            ResearchWorkspace.deleted_at.is_(None),
            ResearchWorkspaceMember.tenant_id == user.tenant_id,
            ResearchWorkspaceMember.user_id == user.id,
            ResearchWorkspaceMember.revoked_at.is_(None),
        )
    )
    if lock:
        query = query.with_for_update(of=ResearchWorkspace)
    row = await db.execute(query)
    result = row.one_or_none()
    if result is None:
        # Return a non-enumerating result for another tenant, matter, or revoked member.
        raise HTTPException(status_code=404, detail="Research workspace not found")
    workspace, member = result
    if role == "write" and member.role not in _WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Workspace role is read-only")
    if role == "owner" and member.role != "owner":
        raise HTTPException(status_code=403, detail="Workspace owner role is required")
    return workspace, member


def _workspace_response(row: ResearchWorkspace, role: str) -> dict:
    return {
        "id": row.id,
        "matter_id": row.matter_id,
        "title": row.title,
        "role": role,
        "deleted_at": row.deleted_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _record_response(row: ResearchRecord) -> dict:
    return {
        name: getattr(row, name)
        for name in (
            "id",
            "record_type",
            "title",
            "body",
            "evidence_class",
            "source_url",
            "source_version",
            "source_as_of",
            "currentness_state",
            "treatment_state",
            "pinpoint",
            "quote",
            "exclusion_reason",
            "assigned_reviewer_id",
            "folder_id",
            "sort_order",
            "revision",
            "created_by_user_id",
            "created_at",
            "updated_at",
        )
    }


def _jsonable(payload: dict) -> dict:
    return {
        key: (
            str(value)
            if isinstance(value, uuid.UUID)
            else value.isoformat()
            if isinstance(value, datetime)
            else value
        )
        for key, value in payload.items()
    }


def _digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


async def _idempotent_response(
    db: AsyncSession, user, operation: str, key: str, request: dict
) -> dict | None:
    existing = (
        await db.execute(
            select(ResearchWorkspaceIdempotency).where(
                ResearchWorkspaceIdempotency.tenant_id == user.tenant_id,
                ResearchWorkspaceIdempotency.actor_user_id == user.id,
                ResearchWorkspaceIdempotency.operation == operation,
                ResearchWorkspaceIdempotency.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.request_sha256 != _digest(request):
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with different input",
        )
    if existing.response_json is None:
        raise HTTPException(
            status_code=409, detail="The matching request is still in progress"
        )
    return existing.response_json


async def _idempotency_lock(db: AsyncSession, user, operation: str, key: str) -> None:
    """Serialize identical creation requests before reserving their durable key."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:value))"),
        {"value": f"research:{user.tenant_id}:{user.id}:{operation}:{key}"},
    )


async def _reserve_idempotency(
    db: AsyncSession, user, operation: str, key: str, request: dict
) -> ResearchWorkspaceIdempotency:
    row = ResearchWorkspaceIdempotency(
        tenant_id=user.tenant_id,
        actor_user_id=user.id,
        operation=operation,
        idempotency_key=key,
        request_sha256=_digest(request),
    )
    db.add(row)
    await db.flush()
    return row


async def _event(
    db: AsyncSession,
    workspace: ResearchWorkspace,
    user,
    action: str,
    *,
    record_id=None,
    detail=None,
) -> None:
    db.add(
        ResearchWorkspaceEvent(
            tenant_id=workspace.tenant_id,
            workspace_id=workspace.id,
            record_id=record_id,
            actor_user_id=user.id,
            action=action,
            detail=detail or {},
        )
    )


async def _assert_same_tenant_user(
    db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    row = await db.execute(
        select(User.id).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=404, detail="Workspace member not found in this tenant"
        )


async def _assert_active_workspace_member(
    db: AsyncSession, workspace: ResearchWorkspace, user_id: uuid.UUID
) -> None:
    row = await db.execute(
        select(ResearchWorkspaceMember.id).where(
            ResearchWorkspaceMember.workspace_id == workspace.id,
            ResearchWorkspaceMember.tenant_id == workspace.tenant_id,
            ResearchWorkspaceMember.user_id == user_id,
            ResearchWorkspaceMember.revoked_at.is_(None),
        )
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=409,
            detail="Assigned reviewer must be an active workspace member",
        )


async def _assert_active_folder(
    db: AsyncSession, workspace: ResearchWorkspace, folder_id: uuid.UUID
) -> None:
    row = await db.execute(
        select(ResearchRecord.id).where(
            ResearchRecord.id == folder_id,
            ResearchRecord.tenant_id == workspace.tenant_id,
            ResearchRecord.workspace_id == workspace.id,
            ResearchRecord.record_type == "folder",
            ResearchRecord.deleted_at.is_(None),
        )
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=409,
            detail="folder_id must name an active folder in this workspace",
        )


@router.post("", status_code=201)
async def create_workspace(
    matter_id: uuid.UUID,
    body: WorkspaceCreate,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=8, max_length=200
    ),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await _matter(matter_id, current_user, db)
    request = {"matter_id": str(matter_id), "title": body.title}
    await _idempotency_lock(db, current_user, "workspace_create", idempotency_key)
    repeated = await _idempotent_response(
        db, current_user, "workspace_create", idempotency_key, request
    )
    if repeated is not None:
        return repeated
    reservation = await _reserve_idempotency(
        db, current_user, "workspace_create", idempotency_key, request
    )
    workspace = ResearchWorkspace(
        tenant_id=current_user.tenant_id,
        matter_id=matter_id,
        title=body.title.strip(),
        created_by_user_id=current_user.id,
    )
    db.add(workspace)
    await db.flush()
    db.add(
        ResearchWorkspaceMember(
            tenant_id=current_user.tenant_id,
            workspace_id=workspace.id,
            user_id=current_user.id,
            role="owner",
        )
    )
    await _event(db, workspace, current_user, "workspace_created")
    reservation.response_json = _jsonable(_workspace_response(workspace, "owner"))
    await db.commit()
    await db.refresh(workspace)
    return reservation.response_json


@router.get("")
async def list_workspaces(
    matter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await _matter(matter_id, current_user, db)
    rows = (
        await db.execute(
            select(ResearchWorkspace, ResearchWorkspaceMember.role)
            .join(
                ResearchWorkspaceMember,
                ResearchWorkspaceMember.workspace_id == ResearchWorkspace.id,
            )
            .where(
                ResearchWorkspace.tenant_id == current_user.tenant_id,
                ResearchWorkspace.matter_id == matter_id,
                ResearchWorkspace.deleted_at.is_(None),
                ResearchWorkspaceMember.user_id == current_user.id,
                ResearchWorkspaceMember.revoked_at.is_(None),
            )
            .order_by(ResearchWorkspace.updated_at.desc())
        )
    ).all()
    return {"items": [_workspace_response(row, role) for row, role in rows]}


@router.delete("/{workspace_id}", status_code=204)
async def archive_workspace(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(
        matter_id, workspace_id, current_user, db, role="owner", lock=True
    )
    before = _jsonable(_workspace_response(workspace, "owner"))
    workspace.deleted_at = datetime.now(timezone.utc)
    after = _jsonable(_workspace_response(workspace, "owner"))
    await _event(
        db,
        workspace,
        current_user,
        "workspace_archived",
        detail={"before": before, "after": after},
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/{workspace_id}/members")
async def list_members(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(matter_id, workspace_id, current_user, db)
    rows = (
        (
            await db.execute(
                select(ResearchWorkspaceMember)
                .where(
                    ResearchWorkspaceMember.workspace_id == workspace.id,
                    ResearchWorkspaceMember.tenant_id == current_user.tenant_id,
                )
                .order_by(ResearchWorkspaceMember.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {"user_id": row.user_id, "role": row.role, "revoked_at": row.revoked_at}
            for row in rows
        ]
    }


@router.put("/{workspace_id}/members")
async def upsert_member(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    body: MemberUpsert,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(
        matter_id, workspace_id, current_user, db, role="owner", lock=True
    )
    await _assert_same_tenant_user(db, current_user.tenant_id, body.user_id)
    row = (
        await db.execute(
            select(ResearchWorkspaceMember).where(
                ResearchWorkspaceMember.workspace_id == workspace.id,
                ResearchWorkspaceMember.user_id == body.user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ResearchWorkspaceMember(
            tenant_id=current_user.tenant_id,
            workspace_id=workspace.id,
            user_id=body.user_id,
            role=body.role,
        )
        db.add(row)
        action = "member_added"
    else:
        if row.role == "owner" and row.revoked_at is None and body.role != "owner":
            owners = (
                await db.execute(
                    select(func.count())
                    .select_from(ResearchWorkspaceMember)
                    .where(
                        ResearchWorkspaceMember.workspace_id == workspace.id,
                        ResearchWorkspaceMember.role == "owner",
                        ResearchWorkspaceMember.revoked_at.is_(None),
                    )
                )
            ).scalar_one()
            if owners <= 1:
                raise HTTPException(
                    status_code=409, detail="A workspace must retain an active owner"
                )
        row.role, row.revoked_at, action = body.role, None, "member_updated"
    await _event(
        db,
        workspace,
        current_user,
        action,
        detail={"member_user_id": str(body.user_id), "role": body.role},
    )
    await db.commit()
    return {"user_id": row.user_id, "role": row.role, "revoked_at": row.revoked_at}


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def revoke_member(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(
        matter_id, workspace_id, current_user, db, role="owner", lock=True
    )
    row = (
        await db.execute(
            select(ResearchWorkspaceMember).where(
                ResearchWorkspaceMember.workspace_id == workspace.id,
                ResearchWorkspaceMember.user_id == user_id,
                ResearchWorkspaceMember.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Active workspace member not found")
    if row.role == "owner":
        owners = (
            await db.execute(
                select(func.count())
                .select_from(ResearchWorkspaceMember)
                .where(
                    ResearchWorkspaceMember.workspace_id == workspace.id,
                    ResearchWorkspaceMember.role == "owner",
                    ResearchWorkspaceMember.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        if owners <= 1:
            raise HTTPException(
                status_code=409, detail="A workspace must retain an active owner"
            )
    row.revoked_at = datetime.now(timezone.utc)
    await _event(
        db,
        workspace,
        current_user,
        "member_revoked",
        detail={"member_user_id": str(user_id)},
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/{workspace_id}/records")
async def list_records(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(matter_id, workspace_id, current_user, db)
    rows = (
        (
            await db.execute(
                select(ResearchRecord)
                .where(
                    ResearchRecord.workspace_id == workspace.id,
                    ResearchRecord.tenant_id == current_user.tenant_id,
                    ResearchRecord.deleted_at.is_(None),
                )
                .order_by(
                    ResearchRecord.record_type,
                    ResearchRecord.sort_order,
                    ResearchRecord.created_at,
                )
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_record_response(row) for row in rows]}


def _record_kwargs(body: RecordCreate) -> dict:
    values = body.model_dump()
    values.pop("revision", None)
    values["source_url"] = (
        str(values["source_url"]) if values.get("source_url") else None
    )
    return values


@router.post("/{workspace_id}/records", status_code=201)
async def create_record(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    body: RecordCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(
        matter_id, workspace_id, current_user, db, role="write"
    )
    if body.assigned_reviewer_id:
        await _assert_active_workspace_member(db, workspace, body.assigned_reviewer_id)
    if body.folder_id:
        await _assert_active_folder(db, workspace, body.folder_id)
    record = ResearchRecord(
        tenant_id=current_user.tenant_id,
        workspace_id=workspace.id,
        created_by_user_id=current_user.id,
        **_record_kwargs(body),
    )
    db.add(record)
    await db.flush()
    after = _jsonable(_record_response(record))
    db.add(
        ResearchRecordRevision(
            tenant_id=workspace.tenant_id,
            workspace_id=workspace.id,
            record_id=record.id,
            revision=record.revision,
            actor_user_id=current_user.id,
            payload=after,
        )
    )
    await _event(
        db,
        workspace,
        current_user,
        "record_created",
        record_id=record.id,
        detail={"after": after},
    )
    await db.commit()
    await db.refresh(record)
    return _record_response(record)


@router.put("/{workspace_id}/records/{record_id}")
async def update_record(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    record_id: uuid.UUID,
    body: RecordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(
        matter_id, workspace_id, current_user, db, role="write"
    )
    record = (
        await db.execute(
            select(ResearchRecord).where(
                ResearchRecord.id == record_id,
                ResearchRecord.workspace_id == workspace.id,
                ResearchRecord.tenant_id == current_user.tenant_id,
                ResearchRecord.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Research record not found")
    if record.revision != body.revision:
        raise HTTPException(
            status_code=409, detail="Research record changed; refresh before saving"
        )
    if body.assigned_reviewer_id:
        await _assert_active_workspace_member(db, workspace, body.assigned_reviewer_id)
    if body.folder_id:
        await _assert_active_folder(db, workspace, body.folder_id)
    before = _jsonable(_record_response(record))
    result = await db.execute(
        update(ResearchRecord)
        .where(
            ResearchRecord.id == record_id,
            ResearchRecord.workspace_id == workspace.id,
            ResearchRecord.tenant_id == current_user.tenant_id,
            ResearchRecord.deleted_at.is_(None),
            ResearchRecord.revision == body.revision,
        )
        .values(
            **_record_kwargs(body),
            revision=ResearchRecord.revision + 1,
            updated_at=func.now(),
        )
        .returning(ResearchRecord)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=409, detail="Research record changed; refresh before saving"
        )
    after = _jsonable(_record_response(record))
    db.add(
        ResearchRecordRevision(
            tenant_id=workspace.tenant_id,
            workspace_id=workspace.id,
            record_id=record.id,
            revision=record.revision,
            actor_user_id=current_user.id,
            payload=after,
        )
    )
    await _event(
        db,
        workspace,
        current_user,
        "record_updated",
        record_id=record.id,
        detail={"before": before, "after": after},
    )
    await db.commit()
    await db.refresh(record)
    return _record_response(record)


@router.delete("/{workspace_id}/records/{record_id}", status_code=204)
async def archive_record(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    record_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, member = await _workspace(
        matter_id, workspace_id, current_user, db, role="write"
    )
    if member.role == "reviewer":
        raise HTTPException(
            status_code=403, detail="Reviewer role cannot archive records"
        )
    record = (
        await db.execute(
            select(ResearchRecord)
            .where(
                ResearchRecord.id == record_id,
                ResearchRecord.workspace_id == workspace.id,
                ResearchRecord.tenant_id == current_user.tenant_id,
                ResearchRecord.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Research record not found")
    if record.record_type == "folder":
        active_child = await db.execute(
            select(ResearchRecord.id)
            .where(
                ResearchRecord.tenant_id == workspace.tenant_id,
                ResearchRecord.workspace_id == workspace.id,
                ResearchRecord.folder_id == record.id,
                ResearchRecord.deleted_at.is_(None),
            )
            .limit(1)
        )
        if active_child.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=409,
                detail="Archive or move active child records before archiving this folder",
            )
    before = _jsonable(_record_response(record))
    try:
        result = await db.execute(
            update(ResearchRecord)
            .where(
                ResearchRecord.id == record_id,
                ResearchRecord.workspace_id == workspace.id,
                ResearchRecord.tenant_id == current_user.tenant_id,
                ResearchRecord.deleted_at.is_(None),
                ResearchRecord.revision == record.revision,
            )
            .values(
                deleted_at=datetime.now(timezone.utc),
                revision=ResearchRecord.revision + 1,
                updated_at=func.now(),
            )
            .returning(ResearchRecord)
        )
    except DBAPIError as exc:
        if "cannot archive a folder with active records" not in str(exc.orig):
            raise
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Archive or move active child records before archiving this folder",
        ) from exc
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=409, detail="Research record changed; refresh before archiving"
        )
    after = _jsonable(_record_response(record))
    db.add(
        ResearchRecordRevision(
            tenant_id=workspace.tenant_id,
            workspace_id=workspace.id,
            record_id=record.id,
            revision=record.revision,
            actor_user_id=current_user.id,
            payload=after,
        )
    )
    await _event(
        db,
        workspace,
        current_user,
        "record_archived",
        record_id=record.id,
        detail={"before": before, "after": after},
    )
    await db.commit()
    return Response(status_code=204)


async def _snapshot_payload(workspace: ResearchWorkspace, db: AsyncSession) -> dict:
    rows = (
        (
            await db.execute(
                select(ResearchRecord)
                .where(
                    ResearchRecord.workspace_id == workspace.id,
                    ResearchRecord.deleted_at.is_(None),
                )
                .order_by(
                    ResearchRecord.record_type,
                    ResearchRecord.sort_order,
                    ResearchRecord.created_at,
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "contract": "lawhand-research-snapshot-v1",
        "workspace": {
            "id": str(workspace.id),
            "matter_id": str(workspace.matter_id),
            "title": workspace.title,
        },
        "records": [
            {
                key: (
                    str(value)
                    if isinstance(value, uuid.UUID)
                    else value.isoformat()
                    if isinstance(value, datetime)
                    else value
                )
                for key, value in _record_response(row).items()
            }
            for row in rows
        ],
        "limitations": [
            "Bluebook-ready export is a reviewable formatting contract, not a guarantee of citation correctness.",
            "Currentness and treatment labels remain the stored evidence state; verify them against the linked source before reliance.",
            "COMP-05 provider-backed resolution and the global explicit-public classification boundary remain outside this workspace.",
        ],
    }


@router.post("/{workspace_id}/snapshots", status_code=201)
async def create_snapshot(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    body: SnapshotCreate,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=8, max_length=200
    ),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(
        matter_id, workspace_id, current_user, db, role="write", lock=True
    )
    request = {
        "matter_id": str(matter_id),
        "workspace_id": str(workspace_id),
        "label": body.label,
    }
    await _idempotency_lock(db, current_user, "snapshot_create", idempotency_key)
    repeated = await _idempotent_response(
        db, current_user, "snapshot_create", idempotency_key, request
    )
    if repeated is not None:
        return repeated
    reservation = await _reserve_idempotency(
        db, current_user, "snapshot_create", idempotency_key, request
    )
    sequence = (
        await db.execute(
            select(
                func.coalesce(func.max(ResearchWorkspaceSnapshot.sequence), 0) + 1
            ).where(ResearchWorkspaceSnapshot.workspace_id == workspace.id)
        )
    ).scalar_one()
    payload = await _snapshot_payload(workspace, db)
    if body.label:
        payload["label"] = body.label
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    snapshot = ResearchWorkspaceSnapshot(
        tenant_id=current_user.tenant_id,
        workspace_id=workspace.id,
        sequence=sequence,
        sha256=hashlib.sha256(encoded).hexdigest(),
        payload=payload,
        created_by_user_id=current_user.id,
    )
    db.add(snapshot)
    await db.flush()
    response = {
        "id": snapshot.id,
        "sequence": snapshot.sequence,
        "sha256": snapshot.sha256,
        "created_at": snapshot.created_at,
    }
    reservation.response_json = _jsonable(response)
    await _event(
        db,
        workspace,
        current_user,
        "snapshot_created",
        detail={
            "snapshot": reservation.response_json,
            "record_count": len(payload["records"]),
        },
    )
    await db.commit()
    await db.refresh(snapshot)
    return reservation.response_json


@router.get("/{workspace_id}/snapshots")
async def list_snapshots(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(matter_id, workspace_id, current_user, db)
    rows = (
        (
            await db.execute(
                select(ResearchWorkspaceSnapshot)
                .where(ResearchWorkspaceSnapshot.workspace_id == workspace.id)
                .order_by(ResearchWorkspaceSnapshot.sequence.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "sequence": row.sequence,
                "sha256": row.sha256,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@router.get("/{workspace_id}/snapshots/{snapshot_id}/export")
async def export_snapshot(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(matter_id, workspace_id, current_user, db)
    snapshot = (
        await db.execute(
            select(ResearchWorkspaceSnapshot).where(
                ResearchWorkspaceSnapshot.id == snapshot_id,
                ResearchWorkspaceSnapshot.workspace_id == workspace.id,
                ResearchWorkspaceSnapshot.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Research snapshot not found")
    filename = f"research-workspace-{workspace.id}-snapshot-{snapshot.sequence}.json"
    return Response(
        json.dumps(snapshot.payload, indent=2, default=str),
        media_type="application/json",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "X-Research-Snapshot-SHA256": snapshot.sha256,
        },
    )


@router.get("/{workspace_id}/history")
async def workspace_history(
    matter_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    workspace, _ = await _workspace(matter_id, workspace_id, current_user, db)
    rows = (
        (
            await db.execute(
                select(ResearchWorkspaceEvent)
                .where(
                    ResearchWorkspaceEvent.workspace_id == workspace.id,
                    ResearchWorkspaceEvent.tenant_id == current_user.tenant_id,
                )
                .order_by(ResearchWorkspaceEvent.created_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "record_id": row.record_id,
                "action": row.action,
                "detail": row.detail,
                "actor_user_id": row.actor_user_id,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }
