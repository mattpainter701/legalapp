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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.mediation import (
    MediationAsset,
    MediationDocument,
    MediationDocumentRecipient,
    MediationInvite,
    MediationParty,
    MediationProposal,
    MediationProposalRecipient,
)
from app.models.plugin import Matter, MediationCase, MediationCaseEvent
from app.models.task import Task
from app.services.task_workflow import append_task_event, transition_task
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
    ProposalReviewRequest,
    ProposalResponse,
    RecipientRelease,
    SessionCreate,
    SessionResponse,
)
from app.services import mediation_service as ms
from app.services.email import (
    EmailDeliveryResult,
    email_delivery_http_error,
    send_portal_invite,
)
from app.services.plugin_entitlements import (
    load_plugin_entitlement,
    plugin_entitlement_is_active,
)
from app.services.rbac_service import get_user_capabilities

settings = get_settings()

MEDIATION_PLUGIN_NAME = "mediation-legal"
INVITE_TTL_DAYS = 14


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _require_mediation_entitlement(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Apply the paid add-on boundary to every firm mediation endpoint."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    entitlement = await load_plugin_entitlement(
        db,
        user.tenant_id,
        MEDIATION_PLUGIN_NAME,
    )
    if plugin_entitlement_is_active(entitlement):
        return
    if entitlement is not None and entitlement.status in {"disabled", "locked"}:
        raise HTTPException(
            status_code=403,
            detail="The Mediation add-on is turned off for this firm",
        )
    raise HTTPException(
        status_code=402,
        detail="The Mediation add-on is not active for this firm",
    )


async def _require_legal_approval(db: AsyncSession, user: User) -> None:
    if "approve_legal_work" not in await get_user_capabilities(db, user.id):
        raise HTTPException(
            status_code=403,
            detail="Legal approval authority is required for this action",
        )


router = APIRouter(
    prefix="/api/plugins/mediation",
    tags=["mediation"],
    dependencies=[Depends(_require_mediation_entitlement)],
)


def _as_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid UUID") from None


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
        next_task = Task(
            tenant_id=user.tenant_id,
            title=body.next_action.strip(),
            task_type="follow_up",
            due_date=body.next_action_due,
            matter_id=matter.id,
            assigned_to_user_id=user.id,
            created_by_user_id=user.id,
            source="mediation_workflow",
        )
        db.add(next_task)
        await db.flush()
        append_task_event(
            db,
            next_task,
            event_type="created",
            actor_user_id=user.id,
            to_status="pending",
        )
        append_task_event(
            db,
            next_task,
            event_type="assigned",
            actor_user_id=user.id,
            metadata={"assigned_to_user_id": str(user.id)},
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
            matter.billing_method = (
                "flat_fee" if case.fixed_fee is not None else matter.billing_method
            )

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
        raise HTTPException(
            status_code=409, detail="Mediation is not linked to a matter"
        )
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Next action title is required")
    if body.priority not in {"low", "medium", "high", "urgent"}:
        raise HTTPException(status_code=400, detail="Invalid priority")

    current_tasks = await _next_task_map(db, [case])
    current = current_tasks.get(case.matter_id)
    if current and body.complete_current:
        transition_task(
            db,
            current,
            to_status="completed",
            actor_user_id=user.id,
            reason="Advanced from mediation work queue",
        )

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
    await db.flush()
    append_task_event(
        db,
        task,
        event_type="created",
        actor_user_id=user.id,
        to_status="pending",
    )
    append_task_event(
        db,
        task,
        event_type="assigned",
        actor_user_id=user.id,
        metadata={"assigned_to_user_id": str(user.id)},
    )
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
    db: AsyncSession,
    asset_id: str,
    case_id: str,
    tenant_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> MediationAsset:
    statement = select(MediationAsset).where(
        MediationAsset.id == asset_id,
        MediationAsset.case_id == case_id,
        MediationAsset.tenant_id == tenant_id,
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
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
    asset = await _get_asset_or_404(
        db, asset_id, case_id, user.tenant_id, for_update=True
    )
    if asset.status not in ("draft", "submitted"):
        raise HTTPException(
            status_code=409,
            detail="Approved or released assets are immutable; create a replacement entry",
        )
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
    asset = await _get_asset_or_404(
        db, asset_id, case_id, user.tenant_id, for_update=True
    )
    if asset.status not in ("draft", "submitted"):
        raise HTTPException(
            status_code=409,
            detail="Approved or released assets cannot be deleted",
        )
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
    await _require_legal_approval(db, user)
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    asset = await _get_asset_or_404(
        db, asset_id, case_id, user.tenant_id, for_update=True
    )
    if asset.status != "submitted":
        raise HTTPException(
            status_code=409,
            detail="Only a submitted asset can be approved",
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
    await _require_legal_approval(db, user)
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    asset = await _get_asset_or_404(
        db, asset_id, case_id, user.tenant_id, for_update=True
    )
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


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"Invalid {label} ID") from None


async def _release_parties(
    db: AsyncSession,
    *,
    case_id: str,
    tenant_id: uuid.UUID,
    party_ids: list[str],
) -> list[MediationParty]:
    parsed = {_parse_uuid(value, "party") for value in party_ids}
    result = await db.execute(
        select(MediationParty).where(
            MediationParty.id.in_(parsed),
            MediationParty.case_id == _parse_uuid(case_id, "case"),
            MediationParty.tenant_id == tenant_id,
        )
    )
    parties = list(result.scalars().all())
    if len(parties) != len(parsed):
        raise HTTPException(status_code=404, detail="Release party not found on case")
    return parties


async def _get_doc_or_404(
    db: AsyncSession,
    doc_id: str,
    case_id: str,
    tenant_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> MediationDocument:
    statement = (
        select(MediationDocument)
        .options(selectinload(MediationDocument.recipients))
        .where(
            MediationDocument.id == doc_id,
            MediationDocument.case_id == case_id,
            MediationDocument.tenant_id == tenant_id,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
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
        .options(selectinload(MediationDocument.recipients))
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
    asset_uuid = None
    if asset_id:
        asset = await _get_asset_or_404(db, asset_id, case_id, user.tenant_id)
        asset_uuid = asset.id

    storage_path, size, content_sha256 = await ms.save_case_upload(
        file, user.tenant_id, case_id, doc_id
    )
    doc = MediationDocument(
        id=doc_id,
        tenant_id=user.tenant_id,
        case_id=uuid.UUID(case_id),
        asset_id=asset_uuid,
        uploaded_by_user_id=user.id,
        filename=os.path.basename(file.filename),
        content_type=file.content_type,
        file_size=size,
        storage_path=storage_path,
        content_sha256=content_sha256,
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
    return await ms.case_document_download_response(doc)


@router.post(
    "/cases/{case_id}/documents/{doc_id}/release",
    response_model=DocumentResponse,
)
async def release_document(
    case_id: str,
    doc_id: str,
    body: RecipientRelease,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _require_legal_approval(db, user)
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    doc = await _get_doc_or_404(db, doc_id, case_id, user.tenant_id, for_update=True)
    if not doc.storage_path or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=409, detail="Document file is unavailable")
    # Release only the exact bytes whose digest was captured at upload.
    await ms.case_document_download_response(doc)

    parties = await _release_parties(
        db,
        case_id=case_id,
        tenant_id=user.tenant_id,
        party_ids=body.party_ids,
    )
    if doc.uploaded_by_party_id and any(
        party.id == doc.uploaded_by_party_id for party in parties
    ):
        raise HTTPException(
            status_code=400,
            detail="A document is already available to its uploading party",
        )
    existing = {recipient.party_id for recipient in doc.recipients}
    added = [party for party in parties if party.id not in existing]
    for party in added:
        recipient = MediationDocumentRecipient(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            document_id=doc.id,
            party_id=party.id,
            released_by_user_id=user.id,
        )
        db.add(recipient)
        doc.recipients.append(recipient)

    if added:
        await _append_event(
            db,
            case,
            event_type="document_release",
            title=f"Released document: {doc.filename}",
            content=f"Recipients: {', '.join(party.name for party in added)}",
            added_by=user.full_name or user.email,
        )
        await db.commit()
    return ms.document_to_response(doc)


@router.delete("/cases/{case_id}/documents/{doc_id}", status_code=204)
async def delete_document(
    case_id: str,
    doc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    doc = await _get_doc_or_404(db, doc_id, case_id, user.tenant_id, for_update=True)
    if doc.recipients:
        raise HTTPException(
            status_code=409,
            detail="Released documents are immutable and cannot be deleted",
        )
    storage_path = doc.storage_path
    await db.delete(doc)
    await db.commit()
    if storage_path and os.path.exists(storage_path):
        try:
            os.remove(storage_path)
            parent = os.path.dirname(storage_path)
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Proposals
# ═══════════════════════════════════════════════════════════════════════════


async def _get_proposal_or_404(
    db: AsyncSession,
    proposal_id: str,
    case_id: str,
    tenant_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> MediationProposal:
    statement = (
        select(MediationProposal)
        .options(selectinload(MediationProposal.recipients))
        .where(
            MediationProposal.id == proposal_id,
            MediationProposal.case_id == case_id,
            MediationProposal.tenant_id == tenant_id,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return proposal


@router.get("/cases/{case_id}/proposals", response_model=List[ProposalResponse])
async def list_proposals(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(MediationProposal)
        .options(selectinload(MediationProposal.recipients))
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

    parent_id = _as_uuid(body.parent_proposal_id)
    if parent_id:
        parent = await _get_proposal_or_404(
            db, str(parent_id), case_id, user.tenant_id, for_update=True
        )
        if parent.status != "open":
            raise HTTPException(
                status_code=409,
                detail="Only an active proposal can receive a counterproposal",
            )

    proposed_by_party_id = _as_uuid(body.proposed_by_party_id)
    if proposed_by_party_id:
        await _get_party_or_404(db, str(proposed_by_party_id), case_id, user.tenant_id)

    proposal = MediationProposal(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        case_id=uuid.UUID(case_id),
        proposed_by_party_id=proposed_by_party_id,
        parent_proposal_id=parent_id,
        title=body.title,
        body=body.body,
        status="open",
        review_state="pending",
        created_by_user_id=user.id,
        content_sha256=ms.proposal_content_sha256(
            title=body.title,
            body=body.body,
            parent_proposal_id=parent_id,
        ),
    )
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    return ms.proposal_to_response(proposal)


@router.post(
    "/cases/{case_id}/proposals/{proposal_id}/review",
    response_model=ProposalResponse,
)
async def review_proposal(
    case_id: str,
    proposal_id: str,
    body: ProposalReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _require_legal_approval(db, user)
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    proposal = await _get_proposal_or_404(
        db, proposal_id, case_id, user.tenant_id, for_update=True
    )
    ms.assert_proposal_integrity(proposal)
    if proposal.status != "open":
        raise HTTPException(
            status_code=409,
            detail="Only an active proposal can be reviewed",
        )
    if proposal.recipients:
        raise HTTPException(
            status_code=409,
            detail="Released proposals are immutable and cannot be re-reviewed",
        )
    if body.decision not in {"approved", "changes_requested", "rejected"}:
        raise HTTPException(status_code=400, detail="Invalid review decision")

    proposal.review_state = body.decision
    proposal.review_notes = body.notes
    proposal.reviewed_by_user_id = user.id
    proposal.reviewed_at = datetime.now(timezone.utc)
    proposal.updated_at = proposal.reviewed_at
    await _append_event(
        db,
        case,
        event_type="proposal_review",
        title=f"Proposal {body.decision.replace('_', ' ')}: {proposal.title}",
        content=body.notes,
        added_by=user.full_name or user.email,
    )
    await db.commit()
    return ms.proposal_to_response(proposal)


@router.post(
    "/cases/{case_id}/proposals/{proposal_id}/release",
    response_model=ProposalResponse,
)
async def release_proposal(
    case_id: str,
    proposal_id: str,
    body: RecipientRelease,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _require_legal_approval(db, user)
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    proposal = await _get_proposal_or_404(
        db, proposal_id, case_id, user.tenant_id, for_update=True
    )
    ms.assert_proposal_integrity(proposal)
    if proposal.status != "open":
        raise HTTPException(
            status_code=409,
            detail="Only an active proposal can be released",
        )
    if proposal.review_state != "approved":
        raise HTTPException(
            status_code=409,
            detail="Proposal must be attorney-approved before release",
        )

    parties = await _release_parties(
        db,
        case_id=case_id,
        tenant_id=user.tenant_id,
        party_ids=body.party_ids,
    )
    if proposal.proposed_by_party_id and any(
        party.id == proposal.proposed_by_party_id for party in parties
    ):
        raise HTTPException(
            status_code=400,
            detail="A proposal cannot be released back to its proposing party",
        )

    existing = {recipient.party_id for recipient in proposal.recipients}
    added = [party for party in parties if party.id not in existing]
    now = datetime.now(timezone.utc)
    for party in added:
        recipient = MediationProposalRecipient(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            proposal_id=proposal.id,
            party_id=party.id,
            released_by_user_id=user.id,
            released_at=now,
        )
        db.add(recipient)
        proposal.recipients.append(recipient)

    if added:
        proposal.released_at = proposal.released_at or now
        proposal.released_by_user_id = proposal.released_by_user_id or user.id
        proposal.updated_at = now
        if proposal.parent_proposal_id:
            parent = await _get_proposal_or_404(
                db,
                str(proposal.parent_proposal_id),
                case_id,
                user.tenant_id,
                for_update=True,
            )
            if parent.status != "open":
                raise HTTPException(
                    status_code=409,
                    detail="The parent proposal is no longer active",
                )
            parent.status = "superseded"
            parent.updated_at = now
        await _append_event(
            db,
            case,
            event_type="proposal_release",
            title=f"Released proposal: {proposal.title}",
            content=f"Recipients: {', '.join(party.name for party in added)}",
            added_by=user.full_name or user.email,
        )
        await db.commit()
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
