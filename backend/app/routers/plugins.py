"""
Legal practice plugin router.

Endpoints:
  GET  /plugins                             — list all plugins
  GET  /plugins/{plugin}/profile            — get practice profile
  PUT  /plugins/{plugin}/profile            — upsert practice profile
  POST /plugins/{plugin}/cold-start         — cold-start interview

  GET  /plugins/litigation/matters          — list matters portfolio
  POST /plugins/litigation/matters          — create matter
  GET  /plugins/litigation/matters/{id}     — get matter detail
  PATCH /plugins/litigation/matters/{id}    — update matter
  POST /plugins/litigation/matters/{id}/events — append event

  GET  /plugins/commercial/renewals         — list renewals
  POST /plugins/commercial/renewals         — create renewal
  PATCH /plugins/commercial/renewals/{id}   — update renewal status
  DELETE /plugins/commercial/renewals/{id}  — delete renewal

  POST /plugins/{plugin}/{skill}            — execute a skill (catch-all, registered last)

NOTE: Static resource routes are registered before the /{plugin}/{skill} catch-all so that
POST /litigation/matters and POST /commercial/renewals are not swallowed by the dynamic route.
"""

import uuid
import re
from datetime import date, datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.conversation import UsageRecord
from app.models.plugin import (
    Estate,
    EstateEvent,
    Matter,
    MatterEvent,
    MediationCase,
    MediationCaseEvent,
    PracticeProfile,
    Renewal,
)
from app.schemas.plugin import (
    EstateCreate,
    EstateEventCreate,
    EstateEventResponse,
    EstateResponse,
    EstateUpdate,
    MatterCreate,
    MatterEventCreate,
    MatterEventResponse,
    MatterResponse,
    MatterUpdate,
    MediationCaseCreate,
    MediationCaseEventCreate,
    MediationCaseEventResponse,
    MediationCaseResponse,
    MediationCaseUpdate,
    PluginInfo,
    PluginListResponse,
    PracticeProfileResponse,
    PracticeProfileUpsert,
    RenewalCreate,
    RenewalResponse,
    RenewalUpdate,
    SkillRequest,
    SkillResponse,
)
from app.services.billing import calculate_cost
from app.services.llm import LLMService
from app.services.plugins.executor import PluginExecutor
from app.services.plugins.prompts import PLUGIN_DISPLAY_NAMES, PLUGIN_SKILLS

settings = get_settings()
router = APIRouter(prefix="/plugins", tags=["plugins"])

# Module-level singletons (same pattern as chat router)
llm_service = LLMService()
plugin_executor = PluginExecutor(llm_service)

# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_PLUGINS = set(PLUGIN_DISPLAY_NAMES.keys())


def _slugify(text: str) -> str:
    """Turn a matter name into a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text[:180]  # keep well under the 200-char limit


def _renewal_urgency(days: int) -> str:
    if days <= 13:
        return "critical"
    if days <= 30:
        return "high"
    if days <= 60:
        return "medium"
    if days <= 90:
        return "low"
    return "monitor"


def _matter_to_response(matter: Matter) -> MatterResponse:
    return MatterResponse(
        id=str(matter.id),
        slug=matter.slug,
        matter_name=matter.matter_name,
        matter_type=matter.matter_type,
        role=matter.role,
        counterparty=matter.counterparty,
        jurisdiction=matter.jurisdiction,
        status=matter.status,
        risk_level=matter.risk_level,
        materiality=matter.materiality,
        conflicts_status=matter.conflicts_status,
        legal_hold_issued=matter.legal_hold_issued,
        is_closed=matter.is_closed,
        created_at=matter.created_at,
        updated_at=matter.updated_at,
    )


def _event_to_response(event: MatterEvent) -> MatterEventResponse:
    return MatterEventResponse(
        id=str(event.id),
        event_type=event.event_type,
        title=event.title,
        content=event.content,
        created_at=event.created_at,
    )


def _renewal_to_response(renewal: Renewal) -> RenewalResponse:
    today = date.today()
    renewal_date = renewal.renewal_date
    # renewal_date may be stored as date or datetime
    if isinstance(renewal_date, datetime):
        renewal_date = renewal_date.date()
    days = (renewal_date - today).days

    notice_deadline = renewal.notice_deadline
    if isinstance(notice_deadline, datetime):
        notice_deadline = notice_deadline.date()

    return RenewalResponse(
        id=str(renewal.id),
        contract_name=renewal.contract_name,
        vendor=renewal.vendor,
        renewal_date=renewal_date,
        notice_deadline=notice_deadline,
        contract_value_annual=(
            float(renewal.contract_value_annual)
            if renewal.contract_value_annual is not None
            else None
        ),
        auto_renewal=renewal.auto_renewal,
        status=renewal.status,
        days_until_renewal=days,
        urgency=_renewal_urgency(days),
        created_at=renewal.created_at,
    )


def _estate_to_response(estate: Estate) -> EstateResponse:
    return EstateResponse(
        id=str(estate.id),
        title=estate.title,
        grantor=estate.grantor,
        estate_type=estate.estate_type,
        status=estate.status,
        summary=estate.summary,
        created_at=estate.created_at,
        updated_at=estate.updated_at,
        events=[
            EstateEventResponse(
                id=str(e.id),
                event_type=e.event_type,
                title=e.title,
                content=e.content,
                created_at=e.created_at,
            )
            for e in (estate.events or [])
        ],
    )


def _estate_event_to_response(event: EstateEvent) -> EstateEventResponse:
    return EstateEventResponse(
        id=str(event.id),
        event_type=event.event_type,
        title=event.title,
        content=event.content,
        created_at=event.created_at,
    )


def _mediation_case_to_response(case: MediationCase) -> MediationCaseResponse:
    return MediationCaseResponse(
        id=str(case.id),
        title=case.title,
        parties=case.parties,
        status=case.status,
        summary=case.summary,
        created_at=case.created_at,
        updated_at=case.updated_at,
        events=[
            MediationCaseEventResponse(
                id=str(e.id),
                event_type=e.event_type,
                title=e.title,
                content=e.content,
                created_at=e.created_at,
            )
            for e in (case.events or [])
        ],
    )


def _mediation_event_to_response(
    event: MediationCaseEvent,
) -> MediationCaseEventResponse:
    return MediationCaseEventResponse(
        id=str(event.id),
        event_type=event.event_type,
        title=event.title,
        content=event.content,
        created_at=event.created_at,
    )


def _validate_plugin(plugin: str) -> None:
    if plugin not in VALID_PLUGINS:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin}' not found. Valid plugins: {sorted(VALID_PLUGINS)}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Plugin listing (no path params)
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all plugins with their skills and profile status for this tenant."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # Fetch all profiles for this tenant in one query
    result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
        )
    )
    profiles = result.scalars().all()
    profile_map = {p.plugin_name: p for p in profiles}

    plugins = []
    for plugin_name, display_name in PLUGIN_DISPLAY_NAMES.items():
        profile = profile_map.get(plugin_name)
        plugins.append(
            PluginInfo(
                plugin_name=plugin_name,
                display_name=display_name,
                skills=PLUGIN_SKILLS.get(plugin_name, []),
                has_profile=profile is not None and bool(profile.profile_content),
                profile_is_complete=profile.is_complete if profile else False,
            )
        )

    return PluginListResponse(plugins=plugins)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Static resource routes (registered BEFORE /{plugin}/{skill})
# These must come before the catch-all dynamic route to avoid being swallowed.
# ═══════════════════════════════════════════════════════════════════════════════

# ── Litigation Matters ────────────────────────────────────────────────────────


@router.get("/litigation/matters", response_model=List[MatterResponse])
async def list_matters(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all matters for the tenant, sorted by updated_at descending."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Matter)
        .where(Matter.tenant_id == user.tenant_id)
        .order_by(Matter.updated_at.desc())
    )
    matters = result.scalars().all()
    return [_matter_to_response(m) for m in matters]


@router.post("/litigation/matters", response_model=MatterResponse, status_code=201)
async def create_matter(
    body: MatterCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new matter and trigger the intake workflow."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # Generate a unique slug within this tenant
    base_slug = _slugify(body.matter_name)
    slug = base_slug
    counter = 1
    while True:
        existing = await db.execute(
            select(Matter).where(
                Matter.tenant_id == user.tenant_id,
                Matter.slug == slug,
            )
        )
        if existing.scalar_one_or_none() is None:
            break
        slug = f"{base_slug}-{counter}"
        counter += 1

    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        slug=slug,
        matter_name=body.matter_name,
        matter_type=body.matter_type,
        role=body.role,
        counterparty=body.counterparty,
        jurisdiction=body.jurisdiction,
        source=body.source,
        status="threatened",
        conflicts_status="not-run",
        legal_hold_issued=False,
        is_closed=False,
    )
    db.add(matter)
    await db.flush()

    # Append an initial event
    event = MatterEvent(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        event_type="intake",
        title="Matter created",
        content=(
            f"Matter '{body.matter_name}' created. "
            f"Counterparty: {body.counterparty}. Source: {body.source}."
        ),
        created_by=user.id,
    )
    db.add(event)

    await db.commit()
    await db.refresh(matter)
    return _matter_to_response(matter)


@router.get("/litigation/matters/{matter_id}", response_model=MatterResponse)
async def get_matter(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get a single matter by ID."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    return _matter_to_response(matter)


@router.patch("/litigation/matters/{matter_id}", response_model=MatterResponse)
async def update_matter(
    matter_id: str,
    body: MatterUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Partially update a matter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(matter, field, value)
    matter.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(matter)
    return _matter_to_response(matter)


@router.post(
    "/litigation/matters/{matter_id}/events",
    response_model=MatterEventResponse,
    status_code=201,
)
async def append_matter_event(
    matter_id: str,
    body: MatterEventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Append an event to the matter's event log."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # Verify matter belongs to this tenant
    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    event = MatterEvent(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        event_type=body.event_type,
        title=body.title,
        content=body.content,
        created_by=user.id,
    )
    db.add(event)

    # Bump matter updated_at
    matter.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(event)
    return _event_to_response(event)


# ── Commercial Renewals ───────────────────────────────────────────────────────


@router.get("/commercial/renewals", response_model=List[RenewalResponse])
async def list_renewals(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all renewals sorted by urgency (soonest first)."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Renewal)
        .where(Renewal.tenant_id == user.tenant_id)
        .order_by(Renewal.renewal_date.asc())
    )
    renewals = result.scalars().all()
    return [_renewal_to_response(r) for r in renewals]


@router.post("/commercial/renewals", response_model=RenewalResponse, status_code=201)
async def create_renewal(
    body: RenewalCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a contract renewal to the register."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    renewal = Renewal(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        contract_name=body.contract_name,
        vendor=body.vendor,
        renewal_date=body.renewal_date,
        notice_deadline=body.notice_deadline,
        contract_value_annual=body.contract_value_annual,
        auto_renewal=body.auto_renewal,
        business_owner=body.business_owner,
        business_owner_email=body.business_owner_email,
        notes=body.notes,
        status="pending",
    )
    db.add(renewal)
    await db.commit()
    await db.refresh(renewal)
    return _renewal_to_response(renewal)


@router.patch("/commercial/renewals/{renewal_id}", response_model=RenewalResponse)
async def update_renewal(
    renewal_id: str,
    body: RenewalUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update renewal status or notes."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Renewal).where(
            Renewal.id == renewal_id,
            Renewal.tenant_id == user.tenant_id,
        )
    )
    renewal = result.scalar_one_or_none()
    if renewal is None:
        raise HTTPException(status_code=404, detail="Renewal not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(renewal, field, value)
    renewal.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(renewal)
    return _renewal_to_response(renewal)


@router.delete("/commercial/renewals/{renewal_id}", status_code=204)
async def delete_renewal(
    renewal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a renewal entry."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Renewal).where(
            Renewal.id == renewal_id,
            Renewal.tenant_id == user.tenant_id,
        )
    )
    renewal = result.scalar_one_or_none()
    if renewal is None:
        raise HTTPException(status_code=404, detail="Renewal not found")

    await db.delete(renewal)
    await db.commit()


# ── Trust & Estate ────────────────────────────────────────────────────────────


@router.get("/trust-estate/estates", response_model=List[EstateResponse])
async def list_estates(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all estates for the tenant, sorted by updated_at descending."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Estate)
        .where(Estate.tenant_id == user.tenant_id)
        .order_by(Estate.updated_at.desc())
    )
    estates = result.scalars().all()
    return [_estate_to_response(e) for e in estates]


@router.post("/trust-estate/estates", response_model=EstateResponse, status_code=201)
async def create_estate(
    body: EstateCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new estate matter."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    estate = Estate(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        title=body.title,
        grantor=body.grantor,
        estate_type=body.estate_type,
        status="active",
        summary=body.summary,
    )
    db.add(estate)
    await db.commit()
    await db.refresh(estate)
    return _estate_to_response(estate)


@router.get("/trust-estate/estates/{estate_id}", response_model=EstateResponse)
async def get_estate(
    estate_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get a single estate by ID."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Estate).where(
            Estate.id == estate_id,
            Estate.tenant_id == user.tenant_id,
        )
    )
    estate = result.scalar_one_or_none()
    if estate is None:
        raise HTTPException(status_code=404, detail="Estate not found")
    return _estate_to_response(estate)


@router.patch("/trust-estate/estates/{estate_id}", response_model=EstateResponse)
async def update_estate(
    estate_id: str,
    body: EstateUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Partially update an estate."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Estate).where(
            Estate.id == estate_id,
            Estate.tenant_id == user.tenant_id,
        )
    )
    estate = result.scalar_one_or_none()
    if estate is None:
        raise HTTPException(status_code=404, detail="Estate not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(estate, field, value)
    estate.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(estate)
    return _estate_to_response(estate)


@router.post(
    "/trust-estate/estates/{estate_id}/events",
    response_model=EstateEventResponse,
    status_code=201,
)
async def append_estate_event(
    estate_id: str,
    body: EstateEventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Append an event to an estate's event log."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Estate).where(
            Estate.id == estate_id,
            Estate.tenant_id == user.tenant_id,
        )
    )
    estate = result.scalar_one_or_none()
    if estate is None:
        raise HTTPException(status_code=404, detail="Estate not found")

    event = EstateEvent(
        id=uuid.uuid4(),
        estate_id=estate.id,
        event_type=body.event_type,
        title=body.title,
        content=body.content,
    )
    db.add(event)
    estate.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(event)
    return _estate_event_to_response(event)


# ── Mediation Cases ───────────────────────────────────────────────────────────


@router.get("/mediation/cases", response_model=List[MediationCaseResponse])
async def list_mediation_cases(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all mediation cases for the tenant, sorted by updated_at descending."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(MediationCase)
        .where(MediationCase.tenant_id == user.tenant_id)
        .order_by(MediationCase.updated_at.desc())
    )
    cases = result.scalars().all()
    return [_mediation_case_to_response(c) for c in cases]


@router.post("/mediation/cases", response_model=MediationCaseResponse, status_code=201)
async def create_mediation_case(
    body: MediationCaseCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new mediation case."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    case = MediationCase(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        title=body.title,
        parties=body.parties,
        status="active",
        summary=body.summary,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return _mediation_case_to_response(case)


@router.get(
    "/mediation/cases/{mediation_case_id}", response_model=MediationCaseResponse
)
async def get_mediation_case(
    mediation_case_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get a single mediation case by ID."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(MediationCase).where(
            MediationCase.id == mediation_case_id,
            MediationCase.tenant_id == user.tenant_id,
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Mediation case not found")
    return _mediation_case_to_response(case)


@router.patch(
    "/mediation/cases/{mediation_case_id}", response_model=MediationCaseResponse
)
async def update_mediation_case(
    mediation_case_id: str,
    body: MediationCaseUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Partially update a mediation case."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(MediationCase).where(
            MediationCase.id == mediation_case_id,
            MediationCase.tenant_id == user.tenant_id,
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Mediation case not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(case, field, value)
    case.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(case)
    return _mediation_case_to_response(case)


@router.post(
    "/mediation/cases/{mediation_case_id}/events",
    response_model=MediationCaseEventResponse,
    status_code=201,
)
async def append_mediation_case_event(
    mediation_case_id: str,
    body: MediationCaseEventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Append an event to a mediation case's event log."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(MediationCase).where(
            MediationCase.id == mediation_case_id,
            MediationCase.tenant_id == user.tenant_id,
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Mediation case not found")

    event = MediationCaseEvent(
        id=uuid.uuid4(),
        case_id=case.id,
        event_type=body.event_type,
        title=body.title,
        content=body.content,
    )
    db.add(event)
    case.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(event)
    return _mediation_event_to_response(event)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Dynamic plugin routes (/{plugin}/...) — registered AFTER statics
# ═══════════════════════════════════════════════════════════════════════════════

# ── Practice Profile CRUD ─────────────────────────────────────────────────────


@router.get("/{plugin}/profile", response_model=PracticeProfileResponse)
async def get_practice_profile(
    plugin: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get the practice profile for a plugin."""
    _validate_plugin(plugin)
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
            PracticeProfile.plugin_name == plugin,
        )
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No practice profile found for plugin '{plugin}'. "
                "Run the cold-start interview first."
            ),
        )

    return PracticeProfileResponse(
        id=str(profile.id),
        plugin_name=profile.plugin_name,
        profile_content=profile.profile_content or "",
        is_complete=profile.is_complete,
        setup_step=profile.setup_step,
        updated_at=profile.updated_at,
    )


@router.put("/{plugin}/profile", response_model=PracticeProfileResponse)
async def upsert_practice_profile(
    plugin: str,
    body: PracticeProfileUpsert,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create or update the practice profile for a plugin."""
    _validate_plugin(plugin)
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
            PracticeProfile.plugin_name == plugin,
        )
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = PracticeProfile(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            plugin_name=plugin,
        )
        db.add(profile)

    profile.profile_content = body.profile_content
    profile.is_complete = body.is_complete
    profile.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(profile)

    return PracticeProfileResponse(
        id=str(profile.id),
        plugin_name=profile.plugin_name,
        profile_content=profile.profile_content or "",
        is_complete=profile.is_complete,
        setup_step=profile.setup_step,
        updated_at=profile.updated_at,
    )


# ── Cold-Start Interview ──────────────────────────────────────────────────────


@router.post("/{plugin}/cold-start", response_model=SkillResponse)
async def cold_start_interview(
    plugin: str,
    body: SkillRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Continue the cold-start setup interview for a plugin."""
    _validate_plugin(plugin)
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    # Load current profile/step
    result = await db.execute(
        select(PracticeProfile).where(
            PracticeProfile.tenant_id == user.tenant_id,
            PracticeProfile.plugin_name == plugin,
        )
    )
    profile = result.scalar_one_or_none()
    current_step = profile.setup_step if profile else 1

    context = body.context or {}
    context["setup_step"] = current_step
    context["tenant_name"] = user.tenant.name if user.tenant else "Legal"

    result_data = await plugin_executor.execute(
        db=db,
        plugin=plugin,
        skill="cold-start-interview",
        input_text=body.input_text,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
        context=context,
        use_premium=body.use_premium,
    )

    # Advance the setup step and persist partial profile
    new_step = min(current_step + 1, 8)
    if profile is None:
        profile = PracticeProfile(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            plugin_name=plugin,
            setup_step=new_step,
        )
        db.add(profile)
    else:
        profile.setup_step = new_step
        profile.updated_at = datetime.now(timezone.utc)

    # If at final step: mark is_complete if no PLACEHOLDERs remain
    if new_step >= 8:
        profile_content = profile.profile_content or ""
        profile.is_complete = "[PLACEHOLDER]" not in profile_content

    # Record usage
    model_used = settings.PREMIUM_LLM if body.use_premium else settings.PRIMARY_LLM
    tokens_in_val = result_data.get("tokens_in", result_data.get("tokens_used", 0) // 2)
    tokens_out_val = result_data.get(
        "tokens_out", result_data.get("tokens_used", 0) // 2
    )
    cost = calculate_cost(
        tokens_in=tokens_in_val,
        tokens_out=tokens_out_val,
        model=model_used,
        billing_tier=user.tenant.billing_tier if user.tenant else "payg",
    )
    usage = UsageRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        conversation_id=None,
        model_used=model_used,
        tokens_in=tokens_in_val,
        tokens_out=tokens_out_val,
        cost_usd=cost,
        operation_type="cold_start",
        query_text=body.input_text[:2000] if body.input_text else None,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
    )
    db.add(usage)

    await db.commit()

    return SkillResponse(**result_data)


# ── Skill Execution (catch-all — must be LAST) ────────────────────────────────


@router.post("/{plugin}/{skill}", response_model=SkillResponse)
async def execute_skill(
    plugin: str,
    skill: str,
    body: SkillRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Execute a named skill for a plugin."""
    _validate_plugin(plugin)

    # Validate skill exists for this plugin
    valid_skills = PLUGIN_SKILLS.get(plugin, [])
    if skill not in valid_skills:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Skill '{skill}' not found for plugin '{plugin}'. "
                f"Valid skills: {valid_skills}"
            ),
        )

    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    context = body.context or {}
    context["tenant_name"] = user.tenant.name if user.tenant else "Legal"

    result_data = await plugin_executor.execute(
        db=db,
        plugin=plugin,
        skill=skill,
        input_text=body.input_text,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
        context=context,
        use_premium=body.use_premium,
    )

    # Record usage
    model_used = settings.PREMIUM_LLM if body.use_premium else settings.PRIMARY_LLM
    tokens_in_val = result_data.get("tokens_in", result_data.get("tokens_used", 0) // 2)
    tokens_out_val = result_data.get(
        "tokens_out", result_data.get("tokens_used", 0) // 2
    )
    cost = calculate_cost(
        tokens_in=tokens_in_val,
        tokens_out=tokens_out_val,
        model=model_used,
        billing_tier=user.tenant.billing_tier if user.tenant else "payg",
    )
    usage = UsageRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        conversation_id=None,
        model_used=model_used,
        tokens_in=tokens_in_val,
        tokens_out=tokens_out_val,
        cost_usd=cost,
        operation_type="plugin_skill",
        query_text=body.input_text[:2000] if body.input_text else None,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
    )
    db.add(usage)
    await db.commit()

    return SkillResponse(**result_data)
