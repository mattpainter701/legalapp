"""Domestic relations (family law) module router.

Full family-law case module: cases plus parties, children, custody arrangements,
support orders, the payment ledger, saved child-support calculation runs,
deadlines, and an activity log — mounted at ``/api/plugins/domestic``. Mirrors
the Estate/Matter router conventions (tenant context on every handler, a
``_get_case_or_404`` guard, soft-delete on cases).

The calculator endpoints bridge to the stateless engine in
``app.services.childsupport``: ``/calculate`` previews a worksheet without
persisting, and ``/cases/{id}/calculations`` runs and saves a reproducible run.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.contact import Contact
from app.models.domestic import (
    ChildSupportCalculation,
    CustodyArrangement,
    DomesticCase,
    DomesticChild,
    DomesticDeadline,
    DomesticEvent,
    DomesticParty,
    SupportOrder,
    SupportPayment,
)
from app.schemas.domestic import (
    CalcRequest,
    CalculationListItem,
    CalculationResponse,
    CalculationSaveRequest,
    ChildCreate,
    ChildResponse,
    ChildUpdate,
    CustodyCreate,
    CustodyResponse,
    CustodyUpdate,
    DeadlineCreate,
    DeadlineResponse,
    DeadlineUpdate,
    DomesticCaseCreate,
    DomesticCaseResponse,
    DomesticCaseStats,
    DomesticCaseUpdate,
    EventCreate,
    EventResponse,
    JurisdictionInfo,
    PartyCreate,
    PartyResponse,
    PartyUpdate,
    PaymentCreate,
    PaymentResponse,
    SupportOrderCreate,
    SupportOrderResponse,
    SupportOrderUpdate,
    WorksheetResponse,
)
from app.services.childsupport import (
    ChildSupportInput,
    ParentFinancials,
    calculate,
)
from app.services.childsupport.engine import UnsupportedJurisdictionError
from app.services.childsupport.registry import list_jurisdictions

router = APIRouter(prefix="/api/plugins/domestic", tags=["domestic"])

_CLOSED_DEADLINES = ("complete", "na")


def _as_uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _contact_name(contact: Contact | None) -> str | None:
    if contact is None:
        return None
    if contact.organization_name:
        return contact.organization_name
    name = " ".join(p for p in (contact.first_name, contact.last_name) if p)
    return name or None


# ── case helpers ────────────────────────────────────────────────────────────


async def _get_case_or_404(
    db: AsyncSession, case_id: str, tenant_id: uuid.UUID
) -> DomesticCase:
    result = await db.execute(
        select(DomesticCase)
        .options(
            selectinload(DomesticCase.children),
            selectinload(DomesticCase.parties),
            selectinload(DomesticCase.deadlines),
            selectinload(DomesticCase.support_orders),
            selectinload(DomesticCase.client),
        )
        .where(
            DomesticCase.id == case_id,
            DomesticCase.tenant_id == tenant_id,
            DomesticCase.is_deleted == False,  # noqa: E712
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _case_to_response(case: DomesticCase) -> DomesticCaseResponse:
    open_deadlines = sorted(
        (
            d
            for d in (case.deadlines or [])
            if (d.status or "").lower() not in _CLOSED_DEADLINES
        ),
        key=lambda d: d.due_date,
    )
    active_orders = [
        o
        for o in (case.support_orders or [])
        if (o.status or "").lower() in ("entered", "active", "modified")
    ]
    current_amount = (
        max((o.monthly_amount or Decimal("0")) for o in active_orders)
        if active_orders
        else None
    )
    return DomesticCaseResponse(
        id=str(case.id),
        case_name=case.case_name,
        case_type=case.case_type,
        status=case.status,
        jurisdiction=case.jurisdiction,
        summary=case.summary,
        county=case.county,
        court_name=case.court_name,
        case_number=case.case_number,
        filed_date=case.filed_date,
        served_date=case.served_date,
        matter_id=str(case.matter_id) if case.matter_id else None,
        client_contact_id=(
            str(case.client_contact_id) if case.client_contact_id else None
        ),
        client_name=_contact_name(case.client),
        children_count=len(case.children or []),
        parties_count=len(case.parties or []),
        next_deadline=open_deadlines[0].due_date if open_deadlines else None,
        current_support_amount=current_amount,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


async def _scope(db: AsyncSession, request: Request):
    """Resolve the current user and bind the tenant RLS context."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))
    return user


async def _ensure_case(
    db: AsyncSession, case_id: str, tenant_id: uuid.UUID
) -> uuid.UUID:
    """Confirm the case exists for this tenant; return its UUID."""
    result = await db.execute(
        select(DomesticCase.id).where(
            DomesticCase.id == case_id,
            DomesticCase.tenant_id == tenant_id,
            DomesticCase.is_deleted == False,  # noqa: E712
        )
    )
    cid = result.scalar_one_or_none()
    if cid is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return cid


# ═══════════════════════════════════════════════════════════════════════════
# Cases
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/cases", response_model=List[DomesticCaseResponse])
async def list_cases(
    request: Request,
    db: AsyncSession = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    user = await _scope(db, request)
    result = await db.execute(
        select(DomesticCase)
        .options(
            selectinload(DomesticCase.children),
            selectinload(DomesticCase.parties),
            selectinload(DomesticCase.deadlines),
            selectinload(DomesticCase.support_orders),
            selectinload(DomesticCase.client),
        )
        .where(
            DomesticCase.tenant_id == user.tenant_id,
            DomesticCase.is_deleted == False,  # noqa: E712
        )
        .order_by(DomesticCase.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [_case_to_response(c) for c in result.scalars().all()]


@router.get("/cases/stats", response_model=DomesticCaseStats)
async def case_stats(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _scope(db, request)
    result = await db.execute(
        select(DomesticCase)
        .options(
            selectinload(DomesticCase.children),
            selectinload(DomesticCase.support_orders),
        )
        .where(
            DomesticCase.tenant_id == user.tenant_id,
            DomesticCase.is_deleted == False,  # noqa: E712
        )
    )
    cases = result.scalars().all()

    def count(status: str) -> int:
        return sum(1 for c in cases if (c.status or "").lower() == status)

    open_orders = sum(
        1
        for c in cases
        for o in (c.support_orders or [])
        if (o.status or "").lower() in ("entered", "active", "modified")
    )
    deadline_result = await db.execute(
        select(DomesticDeadline).where(
            DomesticDeadline.tenant_id == user.tenant_id,
            DomesticDeadline.status.notin_(_CLOSED_DEADLINES),
            DomesticDeadline.due_date >= date.today(),
        )
    )
    upcoming = len(deadline_result.scalars().all())

    return DomesticCaseStats(
        total=len(cases),
        active=count("active"),
        draft=count("draft"),
        pending=count("pending"),
        closed=count("closed"),
        total_children=sum(len(c.children or []) for c in cases),
        open_orders=open_orders,
        upcoming_deadlines=upcoming,
    )


@router.post("/cases", response_model=DomesticCaseResponse, status_code=201)
async def create_case(
    body: DomesticCaseCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _scope(db, request)
    case = DomesticCase(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        case_name=body.case_name,
        case_type=body.case_type,
        status="active",
        jurisdiction=(body.jurisdiction or "ND").upper(),
        summary=body.summary,
        county=body.county,
        court_name=body.court_name,
        case_number=body.case_number,
        filed_date=body.filed_date,
        served_date=body.served_date,
        matter_id=_as_uuid(body.matter_id),
        client_contact_id=_as_uuid(body.client_contact_id),
    )
    db.add(case)
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, str(case.id), user.tenant_id)
    return _case_to_response(case)


@router.get("/cases/{case_id}", response_model=DomesticCaseResponse)
async def get_case(case_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await _scope(db, request)
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    return _case_to_response(case)


@router.patch("/cases/{case_id}", response_model=DomesticCaseResponse)
async def update_case(
    case_id: str,
    body: DomesticCaseUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    data = body.model_dump(exclude_unset=True)
    for fk in ("matter_id", "client_contact_id"):
        if fk in data:
            data[fk] = _as_uuid(data[fk])
    if "jurisdiction" in data and data["jurisdiction"]:
        data["jurisdiction"] = data["jurisdiction"].upper()
    for field, value in data.items():
        setattr(case, field, value)
    case.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    return _case_to_response(case)


@router.delete("/cases/{case_id}", status_code=204)
async def delete_case(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _scope(db, request)
    case = await _get_case_or_404(db, case_id, user.tenant_id)
    case.is_deleted = True
    case.updated_at = datetime.now(timezone.utc)
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Parties
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/cases/{case_id}/parties", response_model=List[PartyResponse])
async def list_parties(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _scope(db, request)
    await _ensure_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(DomesticParty)
        .where(
            DomesticParty.tenant_id == user.tenant_id,
            DomesticParty.case_id == case_id,
        )
        .order_by(DomesticParty.created_at)
    )
    return [PartyResponse.model_validate(p) for p in result.scalars().all()]


@router.post(
    "/cases/{case_id}/parties", response_model=PartyResponse, status_code=201
)
async def create_party(
    case_id: str,
    body: PartyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    cid = await _ensure_case(db, case_id, user.tenant_id)
    data = body.model_dump()
    data["contact_id"] = _as_uuid(data.get("contact_id"))
    party = DomesticParty(
        id=uuid.uuid4(), tenant_id=user.tenant_id, case_id=cid, **data
    )
    db.add(party)
    await db.commit()
    await db.refresh(party)
    return PartyResponse.model_validate(party)


@router.patch(
    "/cases/{case_id}/parties/{party_id}", response_model=PartyResponse
)
async def update_party(
    case_id: str,
    party_id: str,
    body: PartyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    party = await _get_child_row(
        db, DomesticParty, party_id, case_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    if "contact_id" in data:
        data["contact_id"] = _as_uuid(data["contact_id"])
    for field, value in data.items():
        setattr(party, field, value)
    party.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(party)
    return PartyResponse.model_validate(party)


@router.delete("/cases/{case_id}/parties/{party_id}", status_code=204)
async def delete_party(
    case_id: str,
    party_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    party = await _get_child_row(
        db, DomesticParty, party_id, case_id, user.tenant_id
    )
    await db.delete(party)
    await db.commit()


# ── generic sub-row fetch ─────────────────────────────────────────────────────


async def _get_child_row(db, model, row_id, case_id, tenant_id):
    result = await db.execute(
        select(model).where(
            model.id == row_id,
            model.case_id == case_id,
            model.tenant_id == tenant_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return row


# ═══════════════════════════════════════════════════════════════════════════
# Children
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/cases/{case_id}/children", response_model=List[ChildResponse])
async def list_children(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _scope(db, request)
    await _ensure_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(DomesticChild)
        .where(
            DomesticChild.tenant_id == user.tenant_id,
            DomesticChild.case_id == case_id,
        )
        .order_by(DomesticChild.created_at)
    )
    return [ChildResponse.model_validate(c) for c in result.scalars().all()]


@router.post(
    "/cases/{case_id}/children", response_model=ChildResponse, status_code=201
)
async def create_child(
    case_id: str,
    body: ChildCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    cid = await _ensure_case(db, case_id, user.tenant_id)
    data = body.model_dump()
    data["primary_residence_party_id"] = _as_uuid(
        data.get("primary_residence_party_id")
    )
    child = DomesticChild(
        id=uuid.uuid4(), tenant_id=user.tenant_id, case_id=cid, **data
    )
    db.add(child)
    await db.commit()
    await db.refresh(child)
    return ChildResponse.model_validate(child)


@router.patch(
    "/cases/{case_id}/children/{child_id}", response_model=ChildResponse
)
async def update_child(
    case_id: str,
    child_id: str,
    body: ChildUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    child = await _get_child_row(
        db, DomesticChild, child_id, case_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    if "primary_residence_party_id" in data:
        data["primary_residence_party_id"] = _as_uuid(
            data["primary_residence_party_id"]
        )
    for field, value in data.items():
        setattr(child, field, value)
    child.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(child)
    return ChildResponse.model_validate(child)


@router.delete("/cases/{case_id}/children/{child_id}", status_code=204)
async def delete_child(
    case_id: str,
    child_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    child = await _get_child_row(
        db, DomesticChild, child_id, case_id, user.tenant_id
    )
    await db.delete(child)
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Custody arrangements
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/cases/{case_id}/custody", response_model=List[CustodyResponse])
async def list_custody(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _scope(db, request)
    await _ensure_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(CustodyArrangement)
        .where(
            CustodyArrangement.tenant_id == user.tenant_id,
            CustodyArrangement.case_id == case_id,
        )
        .order_by(CustodyArrangement.created_at)
    )
    return [CustodyResponse.model_validate(c) for c in result.scalars().all()]


@router.post(
    "/cases/{case_id}/custody", response_model=CustodyResponse, status_code=201
)
async def create_custody(
    case_id: str,
    body: CustodyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    cid = await _ensure_case(db, case_id, user.tenant_id)
    data = body.model_dump()
    data["primary_party_id"] = _as_uuid(data.get("primary_party_id"))
    row = CustodyArrangement(
        id=uuid.uuid4(), tenant_id=user.tenant_id, case_id=cid, **data
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return CustodyResponse.model_validate(row)


@router.patch(
    "/cases/{case_id}/custody/{custody_id}", response_model=CustodyResponse
)
async def update_custody(
    case_id: str,
    custody_id: str,
    body: CustodyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    row = await _get_child_row(
        db, CustodyArrangement, custody_id, case_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    if "primary_party_id" in data:
        data["primary_party_id"] = _as_uuid(data["primary_party_id"])
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return CustodyResponse.model_validate(row)


@router.delete("/cases/{case_id}/custody/{custody_id}", status_code=204)
async def delete_custody(
    case_id: str,
    custody_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    row = await _get_child_row(
        db, CustodyArrangement, custody_id, case_id, user.tenant_id
    )
    await db.delete(row)
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Support orders + payments
# ═══════════════════════════════════════════════════════════════════════════


def _order_to_response(order: SupportOrder) -> SupportOrderResponse:
    resp = SupportOrderResponse.model_validate(order)
    resp.total_paid = sum(
        (p.amount or Decimal("0")) for p in (order.payments or [])
    ) or Decimal("0")
    return resp


@router.get("/cases/{case_id}/orders", response_model=List[SupportOrderResponse])
async def list_orders(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _scope(db, request)
    await _ensure_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(SupportOrder)
        .options(selectinload(SupportOrder.payments))
        .where(
            SupportOrder.tenant_id == user.tenant_id,
            SupportOrder.case_id == case_id,
        )
        .order_by(SupportOrder.created_at.desc())
    )
    return [_order_to_response(o) for o in result.scalars().all()]


@router.post(
    "/cases/{case_id}/orders", response_model=SupportOrderResponse, status_code=201
)
async def create_order(
    case_id: str,
    body: SupportOrderCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    cid = await _ensure_case(db, case_id, user.tenant_id)
    data = body.model_dump()
    for fk in ("obligor_party_id", "obligee_party_id", "calculation_id"):
        data[fk] = _as_uuid(data.get(fk))
    order = SupportOrder(
        id=uuid.uuid4(), tenant_id=user.tenant_id, case_id=cid, **data
    )
    db.add(order)
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    order = await _get_child_row(
        db, SupportOrder, str(order.id), case_id, user.tenant_id
    )
    return SupportOrderResponse.model_validate(order)


@router.patch(
    "/cases/{case_id}/orders/{order_id}", response_model=SupportOrderResponse
)
async def update_order(
    case_id: str,
    order_id: str,
    body: SupportOrderUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    order = await _get_child_row(
        db, SupportOrder, order_id, case_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    for fk in ("obligor_party_id", "obligee_party_id", "calculation_id"):
        if fk in data:
            data[fk] = _as_uuid(data[fk])
    for field, value in data.items():
        setattr(order, field, value)
    order.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(order)
    return SupportOrderResponse.model_validate(order)


@router.delete("/cases/{case_id}/orders/{order_id}", status_code=204)
async def delete_order(
    case_id: str,
    order_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    order = await _get_child_row(
        db, SupportOrder, order_id, case_id, user.tenant_id
    )
    await db.delete(order)
    await db.commit()


@router.get(
    "/cases/{case_id}/orders/{order_id}/payments",
    response_model=List[PaymentResponse],
)
async def list_payments(
    case_id: str,
    order_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    await _get_child_row(db, SupportOrder, order_id, case_id, user.tenant_id)
    result = await db.execute(
        select(SupportPayment)
        .where(
            SupportPayment.tenant_id == user.tenant_id,
            SupportPayment.order_id == order_id,
        )
        .order_by(SupportPayment.payment_date.desc())
    )
    return [PaymentResponse.model_validate(p) for p in result.scalars().all()]


@router.post(
    "/cases/{case_id}/orders/{order_id}/payments",
    response_model=PaymentResponse,
    status_code=201,
)
async def create_payment(
    case_id: str,
    order_id: str,
    body: PaymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    order = await _get_child_row(
        db, SupportOrder, order_id, case_id, user.tenant_id
    )
    # Default allocation: current first, remainder to arrears.
    amount = body.amount or Decimal("0")
    current = body.applied_to_current
    arrears = body.applied_to_arrears
    if current is None and arrears is None:
        current_due = order.monthly_amount or Decimal("0")
        current = min(amount, current_due)
        arrears = amount - current
    payment = SupportPayment(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        case_id=_as_uuid(case_id),
        order_id=order.id,
        payment_date=body.payment_date,
        amount=amount,
        applied_to_current=current or Decimal("0"),
        applied_to_arrears=arrears or Decimal("0"),
        method=body.method,
        reference_number=body.reference_number,
        notes=body.notes,
    )
    db.add(payment)
    # Reduce arrears balance by the arrears-applied portion.
    if arrears:
        order.arrears_balance = (order.arrears_balance or Decimal("0")) - arrears
        order.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(payment)
    return PaymentResponse.model_validate(payment)


@router.delete(
    "/cases/{case_id}/orders/{order_id}/payments/{payment_id}", status_code=204
)
async def delete_payment(
    case_id: str,
    order_id: str,
    payment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    result = await db.execute(
        select(SupportPayment).where(
            SupportPayment.id == payment_id,
            SupportPayment.order_id == order_id,
            SupportPayment.tenant_id == user.tenant_id,
        )
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    await db.delete(payment)
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Deadlines
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/cases/{case_id}/deadlines", response_model=List[DeadlineResponse])
async def list_deadlines(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _scope(db, request)
    await _ensure_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(DomesticDeadline)
        .where(
            DomesticDeadline.tenant_id == user.tenant_id,
            DomesticDeadline.case_id == case_id,
        )
        .order_by(DomesticDeadline.due_date)
    )
    return [DeadlineResponse.model_validate(d) for d in result.scalars().all()]


@router.post(
    "/cases/{case_id}/deadlines", response_model=DeadlineResponse, status_code=201
)
async def create_deadline(
    case_id: str,
    body: DeadlineCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    cid = await _ensure_case(db, case_id, user.tenant_id)
    data = body.model_dump()
    data["assigned_to"] = _as_uuid(data.get("assigned_to"))
    row = DomesticDeadline(
        id=uuid.uuid4(), tenant_id=user.tenant_id, case_id=cid, **data
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return DeadlineResponse.model_validate(row)


@router.patch(
    "/cases/{case_id}/deadlines/{deadline_id}", response_model=DeadlineResponse
)
async def update_deadline(
    case_id: str,
    deadline_id: str,
    body: DeadlineUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    row = await _get_child_row(
        db, DomesticDeadline, deadline_id, case_id, user.tenant_id
    )
    data = body.model_dump(exclude_unset=True)
    if "assigned_to" in data:
        data["assigned_to"] = _as_uuid(data["assigned_to"])
    if data.get("status") == "complete" and row.completed_at is None:
        row.completed_at = datetime.now(timezone.utc)
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return DeadlineResponse.model_validate(row)


@router.delete("/cases/{case_id}/deadlines/{deadline_id}", status_code=204)
async def delete_deadline(
    case_id: str,
    deadline_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    row = await _get_child_row(
        db, DomesticDeadline, deadline_id, case_id, user.tenant_id
    )
    await db.delete(row)
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Events (activity log)
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/cases/{case_id}/events", response_model=List[EventResponse])
async def list_events(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _scope(db, request)
    await _ensure_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(DomesticEvent)
        .where(
            DomesticEvent.tenant_id == user.tenant_id,
            DomesticEvent.case_id == case_id,
        )
        .order_by(DomesticEvent.created_at.desc())
    )
    return [EventResponse.model_validate(e) for e in result.scalars().all()]


@router.post(
    "/cases/{case_id}/events", response_model=EventResponse, status_code=201
)
async def create_event(
    case_id: str,
    body: EventCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    cid = await _ensure_case(db, case_id, user.tenant_id)
    row = DomesticEvent(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        case_id=cid,
        event_type=body.event_type,
        title=body.title,
        content=body.content,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return EventResponse.model_validate(row)


# ═══════════════════════════════════════════════════════════════════════════
# Child support calculator
# ═══════════════════════════════════════════════════════════════════════════


def _to_engine_input(req: CalcRequest) -> ChildSupportInput:
    parents = [
        ParentFinancials(
            role=p.role,
            name=p.name,
            gross_monthly_income=p.gross_monthly_income,
            federal_income_tax=p.federal_income_tax,
            state_income_tax=p.state_income_tax,
            fica_tax=p.fica_tax,
            required_retirement=p.required_retirement,
            union_dues=p.union_dues,
            health_insurance_children=p.health_insurance_children,
            existing_support_paid=p.existing_support_paid,
            other_children_in_home=p.other_children_in_home,
            is_imputed=p.is_imputed,
            imputed_basis=p.imputed_basis,
            annual_overnights=p.annual_overnights,
        )
        for p in req.parents
    ]
    return ChildSupportInput(
        jurisdiction=(req.jurisdiction or "ND").upper(),
        num_children=req.num_children,
        parents=parents,
        effective_date=req.effective_date or date.today(),
        custody_type=req.custody_type,
        obligor_role=req.obligor_role,
        children_with_parent_a=req.children_with_parent_a,
        deviation_amount=req.deviation_amount,
        deviation_reason=req.deviation_reason,
        allow_estimates=req.allow_estimates,
    )


@router.get("/jurisdictions", response_model=List[JurisdictionInfo])
async def get_jurisdictions(request: Request, db: AsyncSession = Depends(get_db)):
    await _scope(db, request)
    return [JurisdictionInfo(**j) for j in list_jurisdictions()]


@router.post("/calculate", response_model=WorksheetResponse)
async def calculate_preview(
    body: CalcRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    """Stateless worksheet preview — runs the engine without persisting."""
    await _scope(db, request)
    try:
        worksheet = calculate(_to_engine_input(body))
    except UnsupportedJurisdictionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return WorksheetResponse(**worksheet.to_dict())


@router.get(
    "/cases/{case_id}/calculations", response_model=List[CalculationListItem]
)
async def list_calculations(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user = await _scope(db, request)
    await _ensure_case(db, case_id, user.tenant_id)
    result = await db.execute(
        select(ChildSupportCalculation)
        .where(
            ChildSupportCalculation.tenant_id == user.tenant_id,
            ChildSupportCalculation.case_id == case_id,
        )
        .order_by(ChildSupportCalculation.created_at.desc())
    )
    return [
        CalculationListItem.model_validate(c) for c in result.scalars().all()
    ]


@router.post(
    "/cases/{case_id}/calculations",
    response_model=CalculationResponse,
    status_code=201,
)
async def save_calculation(
    case_id: str,
    body: CalculationSaveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    cid = await _ensure_case(db, case_id, user.tenant_id)
    try:
        worksheet = calculate(_to_engine_input(body.request))
    except UnsupportedJurisdictionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    calc = ChildSupportCalculation(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        case_id=cid,
        label=body.label,
        jurisdiction=worksheet.jurisdiction,
        model_type=worksheet.model_type.value,
        schedule_version=worksheet.schedule_version,
        effective_date=worksheet.effective_date,
        num_children=worksheet.num_children,
        obligor_role=worksheet.obligor_role,
        presumptive_amount=worksheet.presumptive_amount,
        final_amount=worksheet.final_amount,
        deviation_amount=worksheet.deviation_amount,
        deviation_reason=worksheet.deviation_reason,
        input_snapshot=body.request.model_dump(mode="json"),
        worksheet=worksheet.to_dict(),
        is_final=body.is_final,
        created_by=user.id,
    )
    db.add(calc)
    await db.commit()
    await db.refresh(calc)
    return CalculationResponse.model_validate(calc)


@router.get(
    "/cases/{case_id}/calculations/{calc_id}",
    response_model=CalculationResponse,
)
async def get_calculation(
    case_id: str,
    calc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    calc = await _get_child_row(
        db, ChildSupportCalculation, calc_id, case_id, user.tenant_id
    )
    return CalculationResponse.model_validate(calc)


@router.get("/cases/{case_id}/calculations/{calc_id}/worksheet.pdf")
async def calculation_worksheet_pdf(
    case_id: str,
    calc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Filing-ready PDF of a saved calculation worksheet."""
    user = await _scope(db, request)
    calc = await _get_child_row(
        db, ChildSupportCalculation, calc_id, case_id, user.tenant_id
    )
    from app.services.childsupport_pdf import generate_worksheet_pdf

    pdf = generate_worksheet_pdf(calc)
    fname = f"child_support_worksheet_{calc_id[:8]}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.delete(
    "/cases/{case_id}/calculations/{calc_id}", status_code=204
)
async def delete_calculation(
    case_id: str,
    calc_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _scope(db, request)
    calc = await _get_child_row(
        db, ChildSupportCalculation, calc_id, case_id, user.tenant_id
    )
    await db.delete(calc)
    await db.commit()
