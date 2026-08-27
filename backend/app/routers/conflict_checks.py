"""Standalone conflict-search workflow with saved, reviewable evidence."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conflict_check import ConflictCheckRecord
from app.models.matter_assignment import MatterAssignment
from app.models.plugin import Matter
from app.schemas.conflict_check import (
    ConflictCheckClose,
    ConflictCheckCreate,
    ConflictCheckList,
    ConflictCheckResponse,
)
from app.services.conflict_check import run_conflict_check
from app.services.conflict_report_pdf import generate_conflict_report_pdf


router = APIRouter(prefix="/api/conflict-checks", tags=["conflict-checks"])
ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _clean_terms(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(raw.strip().split())
        if not value:
            continue
        if len(value) > 300:
            raise HTTPException(status_code=422, detail="Search terms are limited to 300 characters")
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


async def _visible_matter_ids(db: AsyncSession, user) -> set[uuid.UUID] | None:
    if user.role == "admin":
        return None
    assigned = set(
        (
            await db.scalars(
                select(MatterAssignment.matter_id).where(
                    MatterAssignment.tenant_id == user.tenant_id,
                    MatterAssignment.user_id == user.id,
                )
            )
        ).all()
    )
    owned = set(
        (
            await db.scalars(
                select(Matter.id).where(
                    Matter.tenant_id == user.tenant_id,
                    Matter.user_id == user.id,
                )
            )
        ).all()
    )
    return assigned | owned


def _snapshot_matches(raw_matches: list[dict], visible: set[uuid.UUID] | None) -> tuple[list[dict], int]:
    snapshot: list[dict] = []
    total_restricted = 0
    for raw in raw_matches:
        ids = list(raw.get("matter_ids") or [])
        names = list(raw.get("matter_names") or [])
        visible_ids: list[str] = []
        visible_names: list[str] = []
        restricted = 0
        for index, matter_id in enumerate(ids):
            if visible is None or matter_id in visible:
                visible_ids.append(str(matter_id))
                if index < len(names):
                    visible_names.append(str(names[index]))
            else:
                restricted += 1
        total_restricted += restricted
        contact_id = raw.get("contact_id")
        counterparty_only = contact_id in (None, ZERO_UUID, str(ZERO_UUID))
        fully_restricted = bool(ids) and not visible_ids and restricted == len(ids)
        snapshot.append(
            {
                "contact_id": None if counterparty_only else str(contact_id),
                "display_name": (
                    "Restricted potential match"
                    if counterparty_only and fully_restricted
                    else str(raw.get("display_name") or "Potential match")
                ),
                "contact_type": (
                    "restricted" if counterparty_only and fully_restricted else raw.get("contact_type")
                ),
                "email": None if counterparty_only and fully_restricted else raw.get("email"),
                "match_field": raw.get("match_field"),
                "match_value": raw.get("match_value"),
                "matter_ids": visible_ids,
                "matter_names": visible_names,
                "restricted_matter_count": restricted,
            }
        )
    return snapshot, total_restricted


def _to_response(record: ConflictCheckRecord) -> ConflictCheckResponse:
    return ConflictCheckResponse(
        id=str(record.id),
        matter_id=str(record.matter_id) if record.matter_id else None,
        label=record.label,
        query=record.query_snapshot,
        matches=record.result_snapshot,
        match_count=record.match_count,
        restricted_matter_count=record.restricted_matter_count,
        status=record.status,
        decision=record.decision,
        notes=record.notes,
        created_by_user_id=(str(record.created_by_user_id) if record.created_by_user_id else None),
        closed_by_user_id=(str(record.closed_by_user_id) if record.closed_by_user_id else None),
        created_at=record.created_at,
        closed_at=record.closed_at,
    )


async def _record_or_404(db: AsyncSession, record_id: uuid.UUID, user) -> ConflictCheckRecord:
    record = await db.scalar(
        select(ConflictCheckRecord).where(
            ConflictCheckRecord.id == record_id,
            ConflictCheckRecord.tenant_id == user.tenant_id,
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Conflict check not found")
    if user.role != "admin" and record.created_by_user_id != user.id:
        visible = await _visible_matter_ids(db, user)
        if record.matter_id is None or record.matter_id not in visible:
            raise HTTPException(status_code=404, detail="Conflict check not found")
    return record


@router.post("", response_model=ConflictCheckResponse, status_code=201)
async def create_conflict_check(
    body: ConflictCheckCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    names = _clean_terms(body.names)
    emails = _clean_terms(body.emails)
    organizations = _clean_terms(body.organization_names)

    matter_id = None
    if body.matter_id:
        try:
            matter_id = uuid.UUID(body.matter_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid matter ID") from exc
        matter = await db.scalar(
            select(Matter).where(Matter.id == matter_id, Matter.tenant_id == user.tenant_id)
        )
        if matter is None:
            raise HTTPException(status_code=404, detail="Matter not found")
        visible = await _visible_matter_ids(db, user)
        if visible is not None and matter_id not in visible:
            raise HTTPException(status_code=403, detail="You are not assigned to that matter")

    result = await run_conflict_check(
        db=db,
        tenant_id=user.tenant_id,
        names=names,
        emails=emails,
        organization_names=organizations,
    )
    matches, restricted_count = _snapshot_matches(
        result["matches"], await _visible_matter_ids(db, user)
    )
    record = ConflictCheckRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter_id,
        label=body.label.strip(),
        query_snapshot={
            "names": names,
            "emails": emails,
            "organization_names": organizations,
        },
        result_snapshot=matches,
        match_count=len(matches),
        restricted_matter_count=restricted_count,
        created_by_user_id=user.id,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _to_response(record)


@router.get("", response_model=ConflictCheckList)
async def list_conflict_checks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    conditions = [ConflictCheckRecord.tenant_id == user.tenant_id]
    if user.role != "admin":
        visible = await _visible_matter_ids(db, user)
        conditions.append(
            or_(
                ConflictCheckRecord.created_by_user_id == user.id,
                ConflictCheckRecord.matter_id.in_(visible or {ZERO_UUID}),
            )
        )
    total = int(await db.scalar(select(func.count(ConflictCheckRecord.id)).where(*conditions)) or 0)
    rows = list(
        (
            await db.scalars(
                select(ConflictCheckRecord)
                .where(*conditions)
                .order_by(ConflictCheckRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    return ConflictCheckList(items=[_to_response(row) for row in rows], total=total)


@router.get("/{record_id}", response_model=ConflictCheckResponse)
async def get_conflict_check(
    record_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    return _to_response(await _record_or_404(db, record_id, user))


@router.post("/{record_id}/close", response_model=ConflictCheckResponse)
async def close_conflict_check(
    record_id: uuid.UUID,
    body: ConflictCheckClose,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    record = await _record_or_404(db, record_id, user)
    if record.status == "closed":
        raise HTTPException(status_code=409, detail="Closed conflict checks are immutable")
    if not body.acknowledge_attorney_review:
        raise HTTPException(
            status_code=422,
            detail="Confirm that the search is evidence for attorney review, not automatic clearance",
        )
    record.status = "closed"
    record.decision = body.decision
    record.notes = body.notes.strip()
    record.closed_by_user_id = user.id
    record.closed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(record)
    return _to_response(record)


@router.get("/{record_id}/report.pdf")
async def download_conflict_report(
    record_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    record = await _record_or_404(db, record_id, user)
    pdf = generate_conflict_report_pdf(record)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="conflict_check_{record.id}.pdf"',
            "X-Content-Type-Options": "nosniff",
        },
    )
