"""Mediation Platform — firm-side router.

Internal (firm staff) management of mediation cases: case CRUD, the session
log, parties + portal invitations, the marital asset/debt schedule with the
attorney approval workflow, the document vault, and settlement proposals.
Mirrors the Trust & Estate router patterns (``get_current_user`` +
``set_tenant_context`` on every handler, ``_get_case_or_404`` guard, manual
response builders). Path prefix matches the existing frontend skeleton.
"""

import hashlib
import os
import secrets
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.mediation import (
    MediationAsset,
    MediationDocument,
    MediationInvite,
    MediationParty,
    MediationProposal,
)
from app.models.plugin import Matter, MediationCase, MediationCaseEvent
from app.models.task import Task
from app.models.user import User
from app.schemas.mediation import (
    AssetCreate,
    AssetResponse,
    AssetUpdate,
    DocumentResponse,
    InviteResponse,
    MediationCaseCreate,
    MediationCaseDetail,
    MediationNextActionCreate,
    MediationCaseResponse,
    MediationCaseUpdate,
    MediationStats,
    PartyCreate,
    PartyResponse,
    PartyUpdate,
    ProposalCreate,
    ProposalResponse,
    SessionCreate,
    SessionResponse,
)
from app.services import mediation_service as ms
from app.services.email import (
    EmailDeliveryResult,
    email_delivery_http_error,
    send_portal_invite,
)

settings = get_settings()

router = APIRouter(prefix="/api/plugins/mediation", tags=["mediation"])

INVITE_TTL_DAYS = 14


# ── Helpers ─────────────────────────────────────────────────────────────────


def _as_uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _matter_slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or "mediation"
    return f"{base}-{uuid.uuid4().hex[:8]}"


async def _resolve_or_create_matter(
    db: AsyncSession,
    *,
    user: User,
    body: MediationCaseCreate,
) -> Matter:
    """Guarantee every mediation has a tenant-owned operational matter."""
    if body.matter_id:
        matter = (
            await db.execute(
                select(Matter).where(
                    Matter.id == _as_uuid(body.matter_id),
                    Matter.tenant_id == user.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if matter is None:
            raise HTTPException(status_code=404, detail="Matter not found")
        return matter

    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        slug=_matter_slug(body.case_name),
        matter_name=body.case_name,
        description=body.summary,
        matter_type="mediation",
        practice_area="Mediation",
        primary_plugin="mediation-legal",
        jurisdiction=body.jurisdiction,
        court=body.court,
        case_number=body.case_number,
        counterparty=body.party_b,
        stage=body.mediation_stage,
        billing_method="flat_fee" if body.fixed_fee is not None else "hourly",
        budget_amount=body.fixed_fee,
        client_contact_id=_as_uuid(body.client_contact_id),
    )
    db.add(matter)
    return matter


async def _next_task_map(
    db: AsyncSession, cases: list[MediationCase]
) -> dict[uuid.UUID, Task]:
    matter_ids = {case.matter_id for case in cases if case.matter_id}
    if not matter_ids:
        return {}
    tasks = (
        await db.execute(
            select(Task)
            .where(
                Task.matter_id.in_(matter_ids),
                Task.status.in_(("pending", "in_progress")),
            )
            .order_by(Task.due_date.asc().nullslast(), Task.created_at.asc())
        )
    ).scalars()
    next_by_matter: dict[uuid.UUID, Task] = {}
    for task in tasks:
        next_by_matter.setdefault(task.matter_id, task)
    return next_by_matter


async def _get_case_or_404(
    db: AsyncSession, case_id: str, tenant_id: uuid.UUID
) -> MediationCase:
    result = await db.execute(
        select(MediationCase)
        .options(
            selectinload(MediationCase.events),
            selectinload(MediationCase.case_parties),
            selectinload(MediationCase.assets),
        )
        .where(MediationCase.id == case_id, MediationCase.tenant_id == tenant_id)
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Mediation case not found")
    return case


async def _verify_case(db: AsyncSession, case_id: str, tenant_id: uuid.UUID) -> None:
    result = await db.execute(
        select(MediationCase.id).where(
            MediationCase.id == case_id, MediationCase.tenant_id == tenant_id
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Mediation case not found")


async def _append_event(
    db: AsyncSession,
    case: MediationCase,
    *,
    event_type: str,
    title: str,
    content: str | None = None,
    added_by: str | None = None,
    session_type: str | None = None,
) -> MediationCaseEvent:
    event = MediationCaseEvent(
        id=uuid.uuid4(),
        case_id=case.id,
        event_type=event_type,
        session_type=session_type,
        title=title,
        content=content,
        added_by=added_by,
    )
    db.add(event)
    case.updated_at = datetime.now(timezone.utc)
    return event


# ═══════════════════════════════════════════════════════════════════════════
# Cases
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/cases", response_model=List[MediationCaseResponse])
async def list_cases(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(MediationCase)
        .options(
            selectinload(MediationCase.case_parties),
            selectinload(MediationCase.assets),
        )
        .where(MediationCase.tenant_id == user.tenant_id)
        .order_by(MediationCase.updated_at.desc())
    )
    cases = list(result.scalars().all())
    next_tasks = await _next_task_map(db, cases)
    return [ms.case_to_response(c, next_tasks.get(c.matter_id)) for c in cases]


@router.get("/cases/stats", response_model=MediationStats)
async def case_stats(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(MediationCase).where(MediationCase.tenant_id == user.tenant_id)
    )
    cases = result.scalars().all()

    def count(status: str) -> int:
        return sum(1 for c in cases if (c.status or "").lower() == status)

    return MediationStats(
        total=len(cases),
        active=count("active"),
        scheduled=count("scheduled"),
        settled=count("settled"),
        closed=count("closed"),
        pending_confidentiality=sum(1 for c in cases if not c.confidentiality_signed),
    )


@router.post("/cases", response_model=MediationCaseResponse, status_code=201)
async def create_case(
    body: MediationCaseCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    matter = await _resolve_or_create_matter(db, user=user, body=body)

    case = MediationCase(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        title=body.case_name,
        case_name=body.case_name,
        party_a=body.party_a,
        party_b=body.party_b,
        dispute_type=body.dispute_type,
        mediation_stage=body.mediation_stage,
        mediator=body.mediator,
        attorney=body.attorney,
        claim_value=body.claim_value,
        jurisdiction=body.jurisdiction,
        court=body.court,
        case_number=body.case_number,
        waiting_on=body.waiting_on,
        fixed_fee=body.fixed_fee,
        scheduled_session=body.scheduled_session,
        confidentiality_signed=bool(body.confidentiality_signed),
        status="active",
        summary=body.summary,
        matter_id=matter.id,
        client_contact_id=_as_uuid(body.client_contact_id),
    )
    db.add(case)
    if body.next_action and body.next_action.strip():
        db.add(
            Task(
                tenant_id=user.tenant_id,
                title=body.next_action.strip(),
                task_type="follow_up",
                due_date=body.next_action_due,
                matter_id=matter.id,
                assigned_to_user_id=user.id,
                created_by_user_id=user.id,
                source="mediation_workflow",
            )
        )
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, str(case.id), user.tenant_id)
    next_tasks = await _next_task_map(db, [case])
    return ms.case_to_response(case, next_tasks.get(case.matter_id))


@router.get("/cases/{case_id}", response_model=MediationCaseDetail)
async def get_case(case_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    next_tasks = await _next_task_map(db, [case])
    sessions = [
        ms.session_to_response(e)
        for e in sorted(case.events or [], key=lambda e: e.created_at)
    ]
    return MediationCaseDetail(
        mediation=ms.case_to_response(case, next_tasks.get(case.matter_id)),
        sessions=sessions,
    )


@router.patch("/cases/{case_id}", response_model=MediationCaseResponse)
async def update_case(
    case_id: str,
    body: MediationCaseUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)

    update_data = body.model_dump(exclude_unset=True)
    for field in ("matter_id", "client_contact_id"):
        if field in update_data:
            update_data[field] = _as_uuid(update_data[field])
    if update_data.get("matter_id"):
        linked_matter = (
            await db.execute(
                select(Matter).where(
                    Matter.id == update_data["matter_id"],
                    Matter.tenant_id == user.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if linked_matter is None:
            raise HTTPException(status_code=404, detail="Matter not found")
    if update_data.get("case_name"):
        case.title = update_data["case_name"]
    for field, value in update_data.items():
        setattr(case, field, value)
    case.updated_at = datetime.now(timezone.utc)

    if case.matter_id:
        matter = (
            await db.execute(
                select(Matter).where(
                    Matter.id == case.matter_id,
                    Matter.tenant_id == user.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if matter:
            matter.matter_name = case.case_name or case.title
            matter.description = case.summary
            matter.jurisdiction = case.jurisdiction
            matter.court = case.court
            matter.case_number = case.case_number
            matter.counterparty = case.party_b
            matter.stage = case.mediation_stage
            matter.budget_amount = case.fixed_fee
            matter.billing_method = "flat_fee" if case.fixed_fee is not None else matter.billing_method

    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    next_tasks = await _next_task_map(db, [case])
    return ms.case_to_response(case, next_tasks.get(case.matter_id))


@router.post("/cases/{case_id}/next-action", response_model=MediationCaseResponse)
async def set_next_action(
    case_id: str,
    body: MediationNextActionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Advance a mediation's work queue without leaving the case workspace."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    if not case.matter_id:
        raise HTTPException(status_code=409, detail="Mediation is not linked to a matter")
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Next action title is required")
    if body.priority not in {"low", "medium", "high", "urgent"}:
        raise HTTPException(status_code=400, detail="Invalid priority")

    current_tasks = await _next_task_map(db, [case])
    current = current_tasks.get(case.matter_id)
    if current and body.complete_current:
        current.status = "completed"
        current.completed_at = datetime.now(timezone.utc)
        current.closed_by_user_id = user.id
        current.closed_reason = "Advanced from mediation work queue"

    task = Task(
        tenant_id=user.tenant_id,
        title=title,
        task_type="follow_up",
        priority=body.priority,
        due_date=body.due_date,
        matter_id=case.matter_id,
        assigned_to_user_id=user.id,
        created_by_user_id=user.id,
        source="mediation_workflow",
    )
    db.add(task)
    case.waiting_on = body.waiting_on
    case.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    next_tasks = await _next_task_map(db, [case])
    return ms.case_to_response(case, next_tasks.get(case.matter_id))


@router.delete("/cases/{case_id}", status_code=204)
async def delete_case(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    await db.delete(case)
    await db.commit()


# ── Sessions ──────────────────────────────────────────────────────────────


@router.post("/cases/{case_id}/events", response_model=SessionResponse, status_code=201)
async def add_session(
    case_id: str,
    body: SessionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    event = await _append_event(
        db,
        case,
        event_type=body.session_type or "other",
        session_type=body.session_type,
        title=body.title,
        content=body.content,
        added_by=user.full_name or user.email,
    )
    await db.commit()
    await db.refresh(event)
    return ms.session_to_response(event)


# ═══════════════════════════════════════════════════════════════════════════
# Parties + invites
# ═══════════════════════════════════════════════════════════════════════════


async def _get_party_or_404(
    db: AsyncSession, party_id: str, case_id: str, tenant_id: uuid.UUID
) -> MediationParty:
    result = await db.execute(
        select(MediationParty).where(
            MediationParty.id == party_id,
            MediationParty.case_id == case_id,
            MediationParty.tenant_id == tenant_id,
        )
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Party not found")
    return obj


@router.get("/cases/{case_id}/parties", response_model=List[PartyResponse])
async def list_parties(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(MediationParty)
        .where(
            MediationParty.case_id == case_id,
            MediationParty.tenant_id == user.tenant_id,
        )
        .order_by(MediationParty.created_at)
    )
    parties = result.scalars().all()
    # Which parties have an outstanding/accepted invite?
    inv_result = await db.execute(
        select(MediationInvite.party_id).where(
            MediationInvite.case_id == case_id,
            MediationInvite.tenant_id == user.tenant_id,
            MediationInvite.revoked.is_(False),
        )
    )
    invited_ids = {str(pid) for pid in inv_result.scalars().all()}
    return [ms.party_to_response(p, invited=str(p.id) in invited_ids) for p in parties]


@router.post("/cases/{case_id}/parties", response_model=PartyResponse, status_code=201)
async def create_party(
    case_id: str,
    body: PartyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_case(db, case_id, user.tenant_id)
    party = MediationParty(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        case_id=uuid.UUID(case_id),
        role=body.role,
        name=body.name,
        email=body.email,
        contact_id=_as_uuid(body.contact_id),
        is_initiator=body.is_initiator,
    )
    db.add(party)
    await db.commit()
    await db.refresh(party)
    return ms.party_to_response(party)


@router.patch("/cases/{case_id}/parties/{party_id}", response_model=PartyResponse)
async def update_party(
    case_id: str,
    party_id: str,
    body: PartyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    party = await _get_party_or_404(db, party_id, case_id, user.tenant_id)
    update_data = body.model_dump(exclude_unset=True)
    if "contact_id" in update_data:
        update_data["contact_id"] = _as_uuid(update_data["contact_id"])
    for field, value in update_data.items():
        setattr(party, field, value)
    await db.commit()
    await db.refresh(party)
    return ms.party_to_response(party)


@router.delete("/cases/{case_id}/parties/{party_id}", status_code=204)
async def delete_party(
    case_id: str,
    party_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    party = await _get_party_or_404(db, party_id, case_id, user.tenant_id)
    await db.delete(party)
    await db.commit()


@router.post(
    "/cases/{case_id}/parties/{party_id}/invite", response_model=InviteResponse
)
async def invite_party(
    case_id: str,
    party_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Generate a portal invite. Firm clients (our_client) become a login
    (role="client") and get a client_account invite; opposing parties get a
    portal_magic token."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    party = await _get_party_or_404(db, party_id, case_id, user.tenant_id)

    is_client = party.role == "our_client"
    kind = "client_account" if is_client else "portal_magic"

    # Resending rotates access: only the newest invitation/session remains
    # valid for this party.
    existing_invites = await db.execute(
        select(MediationInvite).where(
            MediationInvite.tenant_id == user.tenant_id,
            MediationInvite.case_id == case.id,
            MediationInvite.party_id == party.id,
            MediationInvite.revoked.is_(False),
        )
    )
    for existing_invite in existing_invites.scalars().all():
        existing_invite.revoked = True

    # For firm clients, ensure a User(role="client") exists and link it.
    if is_client and party.email and party.user_id is None:
        existing = await db.execute(
            select(User).where(
                User.email == party.email.lower().strip(),
                User.tenant_id == user.tenant_id,
            )
        )
        client_user = existing.scalar_one_or_none()
        if client_user is None:
            client_user = User(
                id=uuid.uuid4(),
                tenant_id=user.tenant_id,
                email=party.email.lower().strip(),
                full_name=party.name,
                role="client",
                is_active=True,
            )
            db.add(client_user)
            await db.flush()
        party.user_id = client_user.id

    raw_token = secrets.token_urlsafe(32)
    invite = MediationInvite(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        case_id=case.id,
        party_id=party.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        kind=kind,
        email=party.email,
        expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    await _append_event(
        db,
        case,
        event_type="invite",
        title=f"Invited {party.name} to the portal",
        content=f"Role: {party.role}",
        added_by=user.full_name or user.email,
    )
    await db.commit()
    await db.refresh(invite)

    invite_url = f"{settings.FRONTEND_URL.rstrip('/')}/portal/accept?token={raw_token}"
    email_sent = None
    delivery_error = None
    if party.email:
        delivery_result = EmailDeliveryResult.FAILED
        try:
            delivery_result = await send_portal_invite(
                to_email=party.email,
                case_name=case.case_name or case.title,
                invite_url=invite_url,
            )
        except Exception:  # pragma: no cover - email best-effort
            delivery_result = EmailDeliveryResult.FAILED
        email_sent = bool(delivery_result)
        if not email_sent:
            _status_code, delivery_error = email_delivery_http_error(
                delivery_result,
                action="Mediation portal invitation",
            )
            delivery_error += (
                " The invite remains valid; copy and share its link manually."
            )

    return InviteResponse(
        id=str(invite.id),
        party_id=str(party.id),
        kind=kind,
        email=party.email,
        invite_url=invite_url,
        email_sent=email_sent,
        delivery_error=delivery_error,
        expires_at=invite.expires_at,
    )


@router.delete(
    "/cases/{case_id}/parties/{party_id}/invites/{invite_id}", status_code=204
)
async def revoke_portal_invite(
    case_id: str,
    party_id: str,
    invite_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Revoke an invitation and every session linked to it immediately."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    result = await db.execute(
        select(MediationInvite).where(
            MediationInvite.id == invite_id,
            MediationInvite.case_id == case_id,
            MediationInvite.party_id == party_id,
            MediationInvite.tenant_id == user.tenant_id,
        )
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    invite.revoked = True
    await db.commit()
    return Response(status_code=204)


# ═══════════════════════════════════════════════════════════════════════════
# Assets (firm review + approval workflow)
# ═══════════════════════════════════════════════════════════════════════════


async def _get_asset_or_404(
    db: AsyncSession, asset_id: str, case_id: str, tenant_id: uuid.UUID
) -> MediationAsset:
    result = await db.execute(
        select(MediationAsset).where(
            MediationAsset.id == asset_id,
            MediationAsset.case_id == case_id,
            MediationAsset.tenant_id == tenant_id,
        )
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return obj


@router.get("/cases/{case_id}/assets", response_model=List[AssetResponse])
async def list_assets(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(MediationAsset)
        .where(
            MediationAsset.case_id == case_id,
            MediationAsset.tenant_id == user.tenant_id,
        )
        .order_by(MediationAsset.created_at)
    )
    return [ms.asset_to_response(a) for a in result.scalars().all()]


@router.post("/cases/{case_id}/assets", response_model=AssetResponse, status_code=201)
async def create_asset(
    case_id: str,
    body: AssetCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_case(db, case_id, user.tenant_id)
    asset = MediationAsset(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        case_id=uuid.UUID(case_id),
        kind=body.kind,
        category=body.category,
        description=body.description,
        value=body.value,
        owned_by=body.owned_by,
        claimed_by=body.claimed_by,
        notes=body.notes,
        status="draft",
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return ms.asset_to_response(asset)


@router.patch("/cases/{case_id}/assets/{asset_id}", response_model=AssetResponse)
async def update_asset(
    case_id: str,
    asset_id: str,
    body: AssetUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    asset = await _get_asset_or_404(db, asset_id, case_id, user.tenant_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(asset, field, value)
    asset.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(asset)
    return ms.asset_to_response(asset)


@router.delete("/cases/{case_id}/assets/{asset_id}", status_code=204)
async def delete_asset(
    case_id: str,
    asset_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    asset = await _get_asset_or_404(db, asset_id, case_id, user.tenant_id)
    await db.delete(asset)
    await db.commit()


@router.post("/cases/{case_id}/assets/{asset_id}/approve", response_model=AssetResponse)
async def approve_asset(
    case_id: str,
    asset_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    asset = await _get_asset_or_404(db, asset_id, case_id, user.tenant_id)
    if asset.status not in ("submitted", "draft", "attorney_approved"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve an asset in status '{asset.status}'",
        )
    asset.status = "attorney_approved"
    asset.attorney_approved_by_user_id = user.id
    asset.attorney_approved_at = datetime.now(timezone.utc)
    asset.updated_at = asset.attorney_approved_at
    await _append_event(
        db,
        case,
        event_type="approval",
        title=f"Attorney approved: {asset.description}",
        added_by=user.full_name or user.email,
    )
    await db.commit()
    await db.refresh(asset)
    return ms.asset_to_response(asset)


@router.post("/cases/{case_id}/assets/{asset_id}/send", response_model=AssetResponse)
async def send_asset(
    case_id: str,
    asset_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    asset = await _get_asset_or_404(db, asset_id, case_id, user.tenant_id)
    if asset.status != "attorney_approved":
        raise HTTPException(
            status_code=409,
            detail="Asset must be attorney-approved before sending to the opposing party",
        )
    asset.status = "sent"
    asset.sent_at = datetime.now(timezone.utc)
    asset.updated_at = asset.sent_at
    await _append_event(
        db,
        case,
        event_type="sent",
        title=f"Sent to opposing party: {asset.description}",
        added_by=user.full_name or user.email,
    )
    await db.commit()
    await db.refresh(asset)
    return ms.asset_to_response(asset)


# ═══════════════════════════════════════════════════════════════════════════
# Documents (vault)
# ═══════════════════════════════════════════════════════════════════════════


async def _get_doc_or_404(
    db: AsyncSession, doc_id: str, case_id: str, tenant_id: uuid.UUID
) -> MediationDocument:
    result = await db.execute(
        select(MediationDocument).where(
            MediationDocument.id == doc_id,
            MediationDocument.case_id == case_id,
            MediationDocument.tenant_id == tenant_id,
        )
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return obj


@router.get("/cases/{case_id}/documents", response_model=List[DocumentResponse])
async def list_documents(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(MediationDocument)
        .where(
            MediationDocument.case_id == case_id,
            MediationDocument.tenant_id == user.tenant_id,
        )
        .order_by(MediationDocument.created_at.desc())
    )
    return [ms.document_to_response(d) for d in result.scalars().all()]


@router.post(
    "/cases/{case_id}/documents/upload",
    response_model=DocumentResponse,
    status_code=201,
)
async def upload_document(
    case_id: str,
    request: Request,
    file: UploadFile = File(...),
    description: str | None = Form(None),
    asset_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_case(db, case_id, user.tenant_id)

    doc_id = uuid.uuid4()
    storage_path, size = await ms.save_case_upload(
        file, user.tenant_id, case_id, doc_id
    )
    doc = MediationDocument(
        id=doc_id,
        tenant_id=user.tenant_id,
        case_id=uuid.UUID(case_id),
        asset_id=_as_uuid(asset_id),
        uploaded_by_user_id=user.id,
        filename=os.path.basename(file.filename),
        content_type=file.content_type,
        file_size=size,
        storage_path=storage_path,
        description=description,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return ms.document_to_response(doc)


@router.get("/cases/{case_id}/documents/{doc_id}/download")
async def download_document(
    case_id: str,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    doc = await _get_doc_or_404(db, doc_id, case_id, user.tenant_id)
    if not doc.storage_path or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        path=doc.storage_path,
        filename=doc.filename,
        media_type=doc.content_type or "application/octet-stream",
    )


@router.delete("/cases/{case_id}/documents/{doc_id}", status_code=204)
async def delete_document(
    case_id: str,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    doc = await _get_doc_or_404(db, doc_id, case_id, user.tenant_id)
    if doc.storage_path and os.path.exists(doc.storage_path):
        try:
            os.remove(doc.storage_path)
            parent = os.path.dirname(doc.storage_path)
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except OSError:
            pass
    await db.delete(doc)
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Proposals
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/cases/{case_id}/proposals", response_model=List[ProposalResponse])
async def list_proposals(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(MediationProposal)
        .where(
            MediationProposal.case_id == case_id,
            MediationProposal.tenant_id == user.tenant_id,
        )
        .order_by(MediationProposal.created_at)
    )
    proposals = result.scalars().all()
    party_names = await _party_name_map(db, case_id, user.tenant_id)
    return [
        ms.proposal_to_response(p, party_names.get(str(p.proposed_by_party_id)))
        for p in proposals
    ]


@router.post(
    "/cases/{case_id}/proposals", response_model=ProposalResponse, status_code=201
)
async def create_proposal(
    case_id: str,
    body: ProposalCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_case(db, case_id, user.tenant_id)
    proposal = MediationProposal(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        case_id=uuid.UUID(case_id),
        parent_proposal_id=_as_uuid(body.parent_proposal_id),
        title=body.title,
        body=body.body,
        status="open",
    )
    if body.parent_proposal_id:
        parent = await db.get(MediationProposal, _as_uuid(body.parent_proposal_id))
        if parent is not None and parent.tenant_id == user.tenant_id:
            parent.status = "superseded"
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    return ms.proposal_to_response(proposal)


async def _party_name_map(
    db: AsyncSession, case_id: str, tenant_id: uuid.UUID
) -> dict[str, str]:
    result = await db.execute(
        select(MediationParty.id, MediationParty.name).where(
            MediationParty.case_id == case_id,
            MediationParty.tenant_id == tenant_id,
        )
    )
    return {str(pid): name for pid, name in result.all()}
