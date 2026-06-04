"""Trust & Estate administration router.

Full estate-administration module: estates plus fiduciaries, beneficiaries,
asset/liability inventory, distributions, deadlines, and the fiduciary accounting
ledger. Mirrors the Matter module patterns (tenant context on every handler,
``_get_estate_or_404`` guard, manual response builders). Paths are kept identical
to the original skeleton (``/api/plugins/trust-estate/estates``) so the existing
frontend continues to work.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.contact import Contact
from app.models.estate import (
    EstateAccountingEntry,
    EstateAsset,
    EstateBeneficiary,
    EstateDeadline,
    EstateDistribution,
    EstateFiduciary,
    EstateLiability,
)
from app.models.plugin import Estate, EstateEvent
from app.schemas.estate import (
    AccountingEntryCreate,
    AccountingEntryResponse,
    AccountingEntryUpdate,
    AccountingSummary,
    AssetCreate,
    AssetResponse,
    AssetUpdate,
    BeneficiaryCreate,
    BeneficiaryResponse,
    BeneficiaryUpdate,
    DeadlineCreate,
    DeadlineResponse,
    DeadlineUpdate,
    DistributionCreate,
    DistributionResponse,
    DistributionUpdate,
    EstateCreate,
    EstateEventCreate,
    EstateEventResponse,
    EstateResponse,
    EstateStats,
    EstateUpdate,
    FiduciaryCreate,
    FiduciaryResponse,
    FiduciaryUpdate,
    KeyDate,
    LiabilityCreate,
    LiabilityResponse,
    LiabilityUpdate,
)

router = APIRouter(prefix="/api/plugins/trust-estate", tags=["trust-estate"])

_OPEN_STATUSES = ("complete", "na", "cancelled")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _contact_name(contact: Contact | None) -> str | None:
    if contact is None:
        return None
    if contact.organization_name:
        return contact.organization_name
    parts = [contact.first_name, contact.last_name]
    name = " ".join(p for p in parts if p)
    return name or None


def _fmt_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"${value:,.2f}"


async def _get_estate_or_404(
    db: AsyncSession, estate_id: str, tenant_id: uuid.UUID
) -> Estate:
    result = await db.execute(
        select(Estate)
        .options(
            selectinload(Estate.events),
            selectinload(Estate.beneficiaries),
            selectinload(Estate.deadlines),
            selectinload(Estate.client),
        )
        .where(Estate.id == estate_id, Estate.tenant_id == tenant_id)
    )
    estate = result.scalar_one_or_none()
    if estate is None:
        raise HTTPException(status_code=404, detail="Estate not found")
    return estate


def _estate_to_response(estate: Estate) -> EstateResponse:
    deadlines = estate.deadlines or []
    open_deadlines = sorted(
        (d for d in deadlines if d.status not in _OPEN_STATUSES),
        key=lambda d: d.due_date,
    )
    key_dates = [KeyDate(label=d.title, date=d.due_date) for d in open_deadlines[:6]]
    next_key_date = open_deadlines[0].due_date if open_deadlines else None

    return EstateResponse(
        id=str(estate.id),
        estate_name=estate.estate_name or estate.title,
        title=estate.title,
        estate_type=estate.estate_type,
        representative_type=estate.representative_type,
        grantor=estate.grantor,
        status=estate.status,
        summary=estate.summary,
        jurisdiction=estate.jurisdiction,
        domicile_state=estate.domicile_state,
        date_of_death=estate.date_of_death,
        court_name=estate.court_name,
        case_number=estate.case_number,
        gross_estate_value=estate.gross_estate_value,
        net_estate_value=estate.net_estate_value,
        estimated_value=_fmt_money(estate.gross_estate_value),
        matter_id=str(estate.matter_id) if estate.matter_id else None,
        client_contact_id=(
            str(estate.client_contact_id) if estate.client_contact_id else None
        ),
        client_name=_contact_name(estate.client),
        beneficiaries_count=len(estate.beneficiaries or []),
        next_key_date=next_key_date,
        key_dates=key_dates,
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
            for e in sorted(
                estate.events or [], key=lambda e: e.created_at
            )
        ],
    )


def _as_uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


# ═══════════════════════════════════════════════════════════════════════════════
# Estates
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/estates", response_model=List[EstateResponse])
async def list_estates(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Estate)
        .options(
            selectinload(Estate.events),
            selectinload(Estate.beneficiaries),
            selectinload(Estate.deadlines),
            selectinload(Estate.client),
        )
        .where(Estate.tenant_id == user.tenant_id)
        .order_by(Estate.updated_at.desc())
    )
    return [_estate_to_response(e) for e in result.scalars().all()]


@router.get("/estates/stats", response_model=EstateStats)
async def estate_stats(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(Estate)
        .options(selectinload(Estate.beneficiaries))
        .where(Estate.tenant_id == user.tenant_id)
    )
    estates = result.scalars().all()

    def count(status: str) -> int:
        return sum(1 for e in estates if (e.status or "").lower() == status)

    deadline_result = await db.execute(
        select(EstateDeadline).where(
            EstateDeadline.tenant_id == user.tenant_id,
            EstateDeadline.status.notin_(_OPEN_STATUSES),
            EstateDeadline.due_date >= date.today(),
        )
    )
    upcoming = len(deadline_result.scalars().all())

    return EstateStats(
        total=len(estates),
        active=count("active"),
        in_probate=count("in_probate"),
        draft=count("draft"),
        closed=count("closed"),
        total_beneficiaries=sum(len(e.beneficiaries or []) for e in estates),
        total_gross_value=sum(
            (e.gross_estate_value or Decimal("0")) for e in estates
        ),
        upcoming_deadlines=upcoming,
    )


@router.post("/estates", response_model=EstateResponse, status_code=201)
async def create_estate(
    body: EstateCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    estate = Estate(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        title=body.estate_name,
        estate_name=body.estate_name,
        estate_type=body.estate_type,
        representative_type=body.representative_type,
        grantor=body.grantor,
        status="active",
        summary=body.summary,
        jurisdiction=body.jurisdiction,
        domicile_state=body.domicile_state,
        date_of_death=body.date_of_death,
        court_name=body.court_name,
        case_number=body.case_number,
        gross_estate_value=body.gross_estate_value,
        net_estate_value=body.net_estate_value,
        matter_id=_as_uuid(body.matter_id),
        client_contact_id=_as_uuid(body.client_contact_id),
    )
    db.add(estate)
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    estate = await _get_estate_or_404(db, str(estate.id), user.tenant_id)
    return _estate_to_response(estate)


@router.get("/estates/{estate_id}", response_model=EstateResponse)
async def get_estate(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    estate = await _get_estate_or_404(db, estate_id, user.tenant_id)
    return _estate_to_response(estate)


@router.patch("/estates/{estate_id}", response_model=EstateResponse)
async def update_estate(
    estate_id: str,
    body: EstateUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    estate = await _get_estate_or_404(db, estate_id, user.tenant_id)

    update_data = body.model_dump(exclude_unset=True)
    for field in ("matter_id", "client_contact_id"):
        if field in update_data:
            update_data[field] = _as_uuid(update_data[field])
    if "estate_name" in update_data and update_data["estate_name"]:
        estate.title = update_data["estate_name"]
    for field, value in update_data.items():
        setattr(estate, field, value)
    estate.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    estate = await _get_estate_or_404(db, estate_id, user.tenant_id)
    return _estate_to_response(estate)


@router.delete("/estates/{estate_id}", status_code=204)
async def delete_estate(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    estate = await _get_estate_or_404(db, estate_id, user.tenant_id)
    await db.delete(estate)
    await db.commit()


@router.post(
    "/estates/{estate_id}/events",
    response_model=EstateEventResponse,
    status_code=201,
)
async def append_estate_event(
    estate_id: str,
    body: EstateEventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    estate = await _get_estate_or_404(db, estate_id, user.tenant_id)

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
    return EstateEventResponse(
        id=str(event.id),
        event_type=event.event_type,
        title=event.title,
        content=event.content,
        created_at=event.created_at,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Generic sub-resource plumbing
# ═══════════════════════════════════════════════════════════════════════════════


async def _verify_estate(
    db: AsyncSession, estate_id: str, tenant_id: uuid.UUID
) -> None:
    result = await db.execute(
        select(Estate.id).where(
            Estate.id == estate_id, Estate.tenant_id == tenant_id
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Estate not found")


async def _get_child_or_404(db, model, child_id, estate_id, tenant_id):
    result = await db.execute(
        select(model).where(
            model.id == child_id,
            model.estate_id == estate_id,
            model.tenant_id == tenant_id,
        )
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return obj


# ── Fiduciaries ───────────────────────────────────────────────────────────────


def _fiduciary_resp(f: EstateFiduciary) -> FiduciaryResponse:
    return FiduciaryResponse(
        id=str(f.id),
        estate_id=str(f.estate_id),
        name=f.name,
        role=f.role,
        contact_id=str(f.contact_id) if f.contact_id else None,
        appointment_date=f.appointment_date,
        is_primary=f.is_primary,
        compensation_basis=f.compensation_basis,
        compensation_amount=f.compensation_amount,
        email=f.email,
        phone=f.phone,
        notes=f.notes,
        created_at=f.created_at,
    )


@router.get("/estates/{estate_id}/fiduciaries", response_model=List[FiduciaryResponse])
async def list_fiduciaries(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    result = await db.execute(
        select(EstateFiduciary)
        .where(
            EstateFiduciary.estate_id == estate_id,
            EstateFiduciary.tenant_id == user.tenant_id,
        )
        .order_by(EstateFiduciary.is_primary.desc(), EstateFiduciary.created_at)
    )
    return [_fiduciary_resp(f) for f in result.scalars().all()]


@router.post(
    "/estates/{estate_id}/fiduciaries",
    response_model=FiduciaryResponse,
    status_code=201,
)
async def create_fiduciary(
    estate_id: str,
    body: FiduciaryCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    f = EstateFiduciary(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        estate_id=uuid.UUID(estate_id),
        contact_id=_as_uuid(body.contact_id),
        name=body.name,
        role=body.role,
        appointment_date=body.appointment_date,
        is_primary=body.is_primary,
        compensation_basis=body.compensation_basis,
        compensation_amount=body.compensation_amount,
        email=body.email,
        phone=body.phone,
        notes=body.notes,
    )
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return _fiduciary_resp(f)


@router.patch(
    "/estates/{estate_id}/fiduciaries/{child_id}",
    response_model=FiduciaryResponse,
)
async def update_fiduciary(
    estate_id: str,
    child_id: str,
    body: FiduciaryUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    f = await _get_child_or_404(
        db, EstateFiduciary, child_id, estate_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    if "contact_id" in data:
        data["contact_id"] = _as_uuid(data["contact_id"])
    for field, value in data.items():
        setattr(f, field, value)
    await db.commit()
    await db.refresh(f)
    return _fiduciary_resp(f)


@router.delete(
    "/estates/{estate_id}/fiduciaries/{child_id}", status_code=204
)
async def delete_fiduciary(
    estate_id: str,
    child_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    f = await _get_child_or_404(
        db, EstateFiduciary, child_id, estate_id, user.tenant_id
    )
    await db.delete(f)
    await db.commit()


# ── Beneficiaries ─────────────────────────────────────────────────────────────


def _beneficiary_resp(b: EstateBeneficiary) -> BeneficiaryResponse:
    return BeneficiaryResponse(
        id=str(b.id),
        estate_id=str(b.estate_id),
        name=b.name,
        relationship=b.relationship_to_estate,
        contact_id=str(b.contact_id) if b.contact_id else None,
        beneficiary_type=b.beneficiary_type,
        share_percentage=b.share_percentage,
        bequest_description=b.bequest_description,
        is_charity=b.is_charity,
        charity_ein=b.charity_ein,
        email=b.email,
        address=b.address,
        distribution_status=b.distribution_status,
        notes=b.notes,
        created_at=b.created_at,
    )


@router.get(
    "/estates/{estate_id}/beneficiaries", response_model=List[BeneficiaryResponse]
)
async def list_beneficiaries(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    result = await db.execute(
        select(EstateBeneficiary)
        .where(
            EstateBeneficiary.estate_id == estate_id,
            EstateBeneficiary.tenant_id == user.tenant_id,
        )
        .order_by(EstateBeneficiary.created_at)
    )
    return [_beneficiary_resp(b) for b in result.scalars().all()]


@router.post(
    "/estates/{estate_id}/beneficiaries",
    response_model=BeneficiaryResponse,
    status_code=201,
)
async def create_beneficiary(
    estate_id: str,
    body: BeneficiaryCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    b = EstateBeneficiary(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        estate_id=uuid.UUID(estate_id),
        contact_id=_as_uuid(body.contact_id),
        name=body.name,
        relationship_to_estate=body.relationship,
        beneficiary_type=body.beneficiary_type,
        share_percentage=body.share_percentage,
        bequest_description=body.bequest_description,
        is_charity=body.is_charity,
        charity_ein=body.charity_ein,
        email=body.email,
        address=body.address,
        distribution_status=body.distribution_status,
        notes=body.notes,
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return _beneficiary_resp(b)


@router.patch(
    "/estates/{estate_id}/beneficiaries/{child_id}",
    response_model=BeneficiaryResponse,
)
async def update_beneficiary(
    estate_id: str,
    child_id: str,
    body: BeneficiaryUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    b = await _get_child_or_404(
        db, EstateBeneficiary, child_id, estate_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    if "contact_id" in data:
        data["contact_id"] = _as_uuid(data["contact_id"])
    if "relationship" in data:
        b.relationship_to_estate = data.pop("relationship")
    for field, value in data.items():
        setattr(b, field, value)
    await db.commit()
    await db.refresh(b)
    return _beneficiary_resp(b)


@router.delete(
    "/estates/{estate_id}/beneficiaries/{child_id}", status_code=204
)
async def delete_beneficiary(
    estate_id: str,
    child_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    b = await _get_child_or_404(
        db, EstateBeneficiary, child_id, estate_id, user.tenant_id
    )
    await db.delete(b)
    await db.commit()


# ── Assets ────────────────────────────────────────────────────────────────────


@router.get("/estates/{estate_id}/assets", response_model=List[AssetResponse])
async def list_assets(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    result = await db.execute(
        select(EstateAsset)
        .where(
            EstateAsset.estate_id == estate_id,
            EstateAsset.tenant_id == user.tenant_id,
        )
        .order_by(EstateAsset.created_at)
    )
    return list(result.scalars().all())


@router.post(
    "/estates/{estate_id}/assets", response_model=AssetResponse, status_code=201
)
async def create_asset(
    estate_id: str,
    body: AssetCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    a = EstateAsset(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        estate_id=uuid.UUID(estate_id),
        **body.model_dump(),
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


@router.patch(
    "/estates/{estate_id}/assets/{child_id}", response_model=AssetResponse
)
async def update_asset(
    estate_id: str,
    child_id: str,
    body: AssetUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    a = await _get_child_or_404(db, EstateAsset, child_id, estate_id, user.tenant_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(a, field, value)
    await db.commit()
    await db.refresh(a)
    return a


@router.delete("/estates/{estate_id}/assets/{child_id}", status_code=204)
async def delete_asset(
    estate_id: str,
    child_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    a = await _get_child_or_404(db, EstateAsset, child_id, estate_id, user.tenant_id)
    await db.delete(a)
    await db.commit()


# ── Liabilities / claims ──────────────────────────────────────────────────────


@router.get(
    "/estates/{estate_id}/liabilities", response_model=List[LiabilityResponse]
)
async def list_liabilities(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    result = await db.execute(
        select(EstateLiability)
        .where(
            EstateLiability.estate_id == estate_id,
            EstateLiability.tenant_id == user.tenant_id,
        )
        .order_by(EstateLiability.created_at)
    )
    return list(result.scalars().all())


@router.post(
    "/estates/{estate_id}/liabilities",
    response_model=LiabilityResponse,
    status_code=201,
)
async def create_liability(
    estate_id: str,
    body: LiabilityCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    obj = EstateLiability(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        estate_id=uuid.UUID(estate_id),
        **body.model_dump(),
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch(
    "/estates/{estate_id}/liabilities/{child_id}",
    response_model=LiabilityResponse,
)
async def update_liability(
    estate_id: str,
    child_id: str,
    body: LiabilityUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    obj = await _get_child_or_404(
        db, EstateLiability, child_id, estate_id, user.tenant_id
    )
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/estates/{estate_id}/liabilities/{child_id}", status_code=204)
async def delete_liability(
    estate_id: str,
    child_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    obj = await _get_child_or_404(
        db, EstateLiability, child_id, estate_id, user.tenant_id
    )
    await db.delete(obj)
    await db.commit()


# ── Distributions ─────────────────────────────────────────────────────────────


def _distribution_resp(
    d: EstateDistribution, beneficiary_name: str | None = None
) -> DistributionResponse:
    return DistributionResponse(
        id=str(d.id),
        estate_id=str(d.estate_id),
        beneficiary_id=str(d.beneficiary_id),
        beneficiary_name=beneficiary_name,
        asset_id=str(d.asset_id) if d.asset_id else None,
        amount=d.amount,
        distribution_type=d.distribution_type,
        distribution_date=d.distribution_date,
        status=d.status,
        check_number=d.check_number,
        notes=d.notes,
        created_at=d.created_at,
    )


@router.get(
    "/estates/{estate_id}/distributions",
    response_model=List[DistributionResponse],
)
async def list_distributions(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    result = await db.execute(
        select(EstateDistribution)
        .where(
            EstateDistribution.estate_id == estate_id,
            EstateDistribution.tenant_id == user.tenant_id,
        )
        .order_by(EstateDistribution.created_at)
    )
    distributions = result.scalars().all()
    ben_result = await db.execute(
        select(EstateBeneficiary.id, EstateBeneficiary.name).where(
            EstateBeneficiary.estate_id == estate_id,
            EstateBeneficiary.tenant_id == user.tenant_id,
        )
    )
    names = {str(bid): name for bid, name in ben_result.all()}
    return [
        _distribution_resp(d, names.get(str(d.beneficiary_id)))
        for d in distributions
    ]


@router.post(
    "/estates/{estate_id}/distributions",
    response_model=DistributionResponse,
    status_code=201,
)
async def create_distribution(
    estate_id: str,
    body: DistributionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    d = EstateDistribution(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        estate_id=uuid.UUID(estate_id),
        beneficiary_id=uuid.UUID(body.beneficiary_id),
        asset_id=_as_uuid(body.asset_id),
        amount=body.amount,
        distribution_type=body.distribution_type,
        distribution_date=body.distribution_date,
        status=body.status,
        check_number=body.check_number,
        notes=body.notes,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return _distribution_resp(d)


@router.patch(
    "/estates/{estate_id}/distributions/{child_id}",
    response_model=DistributionResponse,
)
async def update_distribution(
    estate_id: str,
    child_id: str,
    body: DistributionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    d = await _get_child_or_404(
        db, EstateDistribution, child_id, estate_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    for field in ("beneficiary_id", "asset_id"):
        if field in data:
            data[field] = _as_uuid(data[field])
    for field, value in data.items():
        setattr(d, field, value)
    await db.commit()
    await db.refresh(d)
    return _distribution_resp(d)


@router.delete("/estates/{estate_id}/distributions/{child_id}", status_code=204)
async def delete_distribution(
    estate_id: str,
    child_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    d = await _get_child_or_404(
        db, EstateDistribution, child_id, estate_id, user.tenant_id
    )
    await db.delete(d)
    await db.commit()


# ── Deadlines ─────────────────────────────────────────────────────────────────


@router.get("/estates/{estate_id}/deadlines", response_model=List[DeadlineResponse])
async def list_deadlines(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    result = await db.execute(
        select(EstateDeadline)
        .where(
            EstateDeadline.estate_id == estate_id,
            EstateDeadline.tenant_id == user.tenant_id,
        )
        .order_by(EstateDeadline.due_date)
    )
    return [_deadline_resp(d) for d in result.scalars().all()]


def _deadline_resp(d: EstateDeadline) -> DeadlineResponse:
    return DeadlineResponse(
        id=str(d.id),
        estate_id=str(d.estate_id),
        title=d.title,
        deadline_type=d.deadline_type,
        due_date=d.due_date,
        status=d.status,
        assigned_to=str(d.assigned_to) if d.assigned_to else None,
        completed_at=d.completed_at,
        notes=d.notes,
        created_at=d.created_at,
    )


@router.post(
    "/estates/{estate_id}/deadlines",
    response_model=DeadlineResponse,
    status_code=201,
)
async def create_deadline(
    estate_id: str,
    body: DeadlineCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    d = EstateDeadline(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        estate_id=uuid.UUID(estate_id),
        title=body.title,
        deadline_type=body.deadline_type,
        due_date=body.due_date,
        status=body.status,
        assigned_to=_as_uuid(body.assigned_to),
        notes=body.notes,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return _deadline_resp(d)


@router.patch(
    "/estates/{estate_id}/deadlines/{child_id}", response_model=DeadlineResponse
)
async def update_deadline(
    estate_id: str,
    child_id: str,
    body: DeadlineUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    d = await _get_child_or_404(
        db, EstateDeadline, child_id, estate_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    if "assigned_to" in data:
        data["assigned_to"] = _as_uuid(data["assigned_to"])
    if data.get("status") == "complete" and d.completed_at is None:
        d.completed_at = datetime.now(timezone.utc)
    for field, value in data.items():
        setattr(d, field, value)
    await db.commit()
    await db.refresh(d)
    return _deadline_resp(d)


@router.delete("/estates/{estate_id}/deadlines/{child_id}", status_code=204)
async def delete_deadline(
    estate_id: str,
    child_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    d = await _get_child_or_404(
        db, EstateDeadline, child_id, estate_id, user.tenant_id
    )
    await db.delete(d)
    await db.commit()


# ── Fiduciary accounting ──────────────────────────────────────────────────────


@router.get(
    "/estates/{estate_id}/accounting",
    response_model=List[AccountingEntryResponse],
)
async def list_accounting(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    result = await db.execute(
        select(EstateAccountingEntry)
        .where(
            EstateAccountingEntry.estate_id == estate_id,
            EstateAccountingEntry.tenant_id == user.tenant_id,
        )
        .order_by(
            EstateAccountingEntry.entry_date.desc(),
            EstateAccountingEntry.created_at.desc(),
        )
    )
    return list(result.scalars().all())


@router.get(
    "/estates/{estate_id}/accounting/summary", response_model=AccountingSummary
)
async def accounting_summary(
    estate_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    result = await db.execute(
        select(EstateAccountingEntry).where(
            EstateAccountingEntry.estate_id == estate_id,
            EstateAccountingEntry.tenant_id == user.tenant_id,
        )
    )
    entries = result.scalars().all()
    return _compute_accounting_summary(entries)


def _compute_accounting_summary(entries) -> AccountingSummary:
    z = Decimal("0")
    receipts = sum((e.amount for e in entries if e.entry_type == "receipt"), z)
    disbursements = sum(
        (e.amount for e in entries if e.entry_type == "disbursement"), z
    )
    gains = sum((e.amount for e in entries if e.entry_type == "gain"), z)
    losses = sum((e.amount for e in entries if e.entry_type == "loss"), z)
    distributions = sum(
        (e.amount for e in entries if e.entry_type == "distribution"), z
    )

    def signed(e) -> Decimal:
        if e.entry_type in ("receipt", "gain"):
            return e.amount
        return -e.amount

    principal = sum(
        (signed(e) for e in entries if e.account_class == "principal"), z
    )
    income = sum((signed(e) for e in entries if e.account_class == "income"), z)

    return AccountingSummary(
        principal_balance=principal,
        income_balance=income,
        total_receipts=receipts,
        total_disbursements=disbursements,
        total_gains=gains,
        total_losses=losses,
        total_distributions=distributions,
        entry_count=len(entries),
    )


@router.post(
    "/estates/{estate_id}/accounting",
    response_model=AccountingEntryResponse,
    status_code=201,
)
async def create_accounting_entry(
    estate_id: str,
    body: AccountingEntryCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    await _verify_estate(db, estate_id, user.tenant_id)
    e = EstateAccountingEntry(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        estate_id=uuid.UUID(estate_id),
        entry_date=body.entry_date,
        entry_type=body.entry_type,
        account_class=body.account_class,
        amount=body.amount,
        description=body.description,
        payee_payor=body.payee_payor,
        asset_id=_as_uuid(body.asset_id),
        reference_number=body.reference_number,
        created_by=user.id,
        notes=body.notes,
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


@router.patch(
    "/estates/{estate_id}/accounting/{child_id}",
    response_model=AccountingEntryResponse,
)
async def update_accounting_entry(
    estate_id: str,
    child_id: str,
    body: AccountingEntryUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    e = await _get_child_or_404(
        db, EstateAccountingEntry, child_id, estate_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    if "asset_id" in data:
        data["asset_id"] = _as_uuid(data["asset_id"])
    for field, value in data.items():
        setattr(e, field, value)
    await db.commit()
    await db.refresh(e)
    return e


@router.delete("/estates/{estate_id}/accounting/{child_id}", status_code=204)
async def delete_accounting_entry(
    estate_id: str,
    child_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    e = await _get_child_or_404(
        db, EstateAccountingEntry, child_id, estate_id, user.tenant_id
    )
    await db.delete(e)
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Reports
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/estates/{estate_id}/reports/{kind}")
async def estate_report(
    estate_id: str,
    kind: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return structured report JSON for inventory, accounting, distribution, or deadlines."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    estate = await _get_estate_or_404(db, estate_id, user.tenant_id)

    if kind == "inventory":
        assets = (
            await db.execute(
                select(EstateAsset).where(
                    EstateAsset.estate_id == estate_id,
                    EstateAsset.tenant_id == user.tenant_id,
                )
            )
        ).scalars().all()
        liabilities = (
            await db.execute(
                select(EstateLiability).where(
                    EstateLiability.estate_id == estate_id,
                    EstateLiability.tenant_id == user.tenant_id,
                )
            )
        ).scalars().all()
        total_assets = sum(
            (a.current_value or a.date_of_death_value or Decimal("0") for a in assets),
            Decimal("0"),
        )
        total_liabilities = sum((lia.amount for lia in liabilities), Decimal("0"))
        return {
            "report": "inventory",
            "estate_name": estate.estate_name or estate.title,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_assets": str(total_assets),
            "total_liabilities": str(total_liabilities),
            "net_value": str(total_assets - total_liabilities),
            "assets": [
                {
                    "name": a.name,
                    "category": a.category,
                    "ownership_type": a.ownership_type,
                    "date_of_death_value": str(a.date_of_death_value or ""),
                    "current_value": str(a.current_value or ""),
                    "is_probate": a.is_probate,
                }
                for a in assets
            ],
            "liabilities": [
                {
                    "creditor_name": lia.creditor_name,
                    "claim_type": lia.claim_type,
                    "amount": str(lia.amount),
                    "status": lia.status,
                }
                for lia in liabilities
            ],
        }

    if kind == "accounting":
        entries = (
            await db.execute(
                select(EstateAccountingEntry).where(
                    EstateAccountingEntry.estate_id == estate_id,
                    EstateAccountingEntry.tenant_id == user.tenant_id,
                ).order_by(EstateAccountingEntry.entry_date)
            )
        ).scalars().all()
        summary = _compute_accounting_summary(entries)
        return {
            "report": "accounting",
            "estate_name": estate.estate_name or estate.title,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary.model_dump(mode="json"),
            "entries": [
                {
                    "entry_date": e.entry_date.isoformat(),
                    "entry_type": e.entry_type,
                    "account_class": e.account_class,
                    "amount": str(e.amount),
                    "description": e.description,
                    "payee_payor": e.payee_payor,
                }
                for e in entries
            ],
        }

    if kind == "distribution":
        bens = (
            await db.execute(
                select(EstateBeneficiary).where(
                    EstateBeneficiary.estate_id == estate_id,
                    EstateBeneficiary.tenant_id == user.tenant_id,
                )
            )
        ).scalars().all()
        dists = (
            await db.execute(
                select(EstateDistribution).where(
                    EstateDistribution.estate_id == estate_id,
                    EstateDistribution.tenant_id == user.tenant_id,
                )
            )
        ).scalars().all()
        paid_by_ben: dict[str, Decimal] = {}
        for d in dists:
            if d.status == "paid":
                key = str(d.beneficiary_id)
                paid_by_ben[key] = paid_by_ben.get(key, Decimal("0")) + d.amount
        return {
            "report": "distribution",
            "estate_name": estate.estate_name or estate.title,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "beneficiaries": [
                {
                    "name": b.name,
                    "beneficiary_type": b.beneficiary_type,
                    "share_percentage": str(b.share_percentage or ""),
                    "distribution_status": b.distribution_status,
                    "total_distributed": str(paid_by_ben.get(str(b.id), Decimal("0"))),
                }
                for b in bens
            ],
        }

    if kind == "deadlines":
        deadlines = (
            await db.execute(
                select(EstateDeadline).where(
                    EstateDeadline.estate_id == estate_id,
                    EstateDeadline.tenant_id == user.tenant_id,
                ).order_by(EstateDeadline.due_date)
            )
        ).scalars().all()
        return {
            "report": "deadlines",
            "estate_name": estate.estate_name or estate.title,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "deadlines": [
                {
                    "title": d.title,
                    "deadline_type": d.deadline_type,
                    "due_date": d.due_date.isoformat(),
                    "status": d.status,
                }
                for d in deadlines
            ],
        }

    raise HTTPException(status_code=404, detail=f"Unknown report kind: {kind}")
