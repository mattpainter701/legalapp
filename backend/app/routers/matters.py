"""Matter/case management router — firm-wide matters with assignments, notes, retainers, billing."""

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.billing import TimeEntry, Invoice, Payment
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact
from app.models.matter_assignment import MatterAssignment
from app.models.matter_note import MatterNote
from app.models.plugin import Matter, MatterEvent
from app.models.retainer import Retainer, RetainerTransaction
from app.models.task import Task
from app.models.user import User
from app.models.tenant import Tenant
from app.services.email import EmailService
from app.services.cloud_init import initialize_matter_folders, share_matter_folders
from app.schemas.matter import (
    BudgetUtilization,
    MatterAssignmentCreate,
    MatterAssignmentResponse,
    MatterCloudFolderStatus,
    MatterCreate,
    MatterListResponse,
    MatterMemoryResponse,
    MatterMemoryUpdate,
    MatterNoteCreate,
    MatterNoteResponse,
    MatterNoteUpdate,
    MatterResponse,
    MatterStats,
    MatterSummary,
    MatterSummaryMyMatters,
    MatterUpdate,
    RetainerCreate,
    RetainerDrawdownRequest,
    RetainerResponse,
    RetainerTransactionResponse,
    TimelineEntry,
)
from app.services.plugins.manifest import get_plugin_manifest
from app.models.tenant_credential import TenantCredential
from app.services.cloud_search import CloudSearchService

_cloud_search = CloudSearchService()

router = APIRouter(prefix="/api/matters", tags=["matters"])
logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_matter_or_404(
    db: AsyncSession, matter_id: str, tenant_id: uuid.UUID
) -> Matter:
    """Fetch a matter by ID, verifying tenant ownership, or raise 404."""
    result = await db.execute(
        select(Matter)
        .options(
            selectinload(Matter.assignments).selectinload(MatterAssignment.user),
            selectinload(Matter.client),
            selectinload(Matter.attorney_of_record),
            selectinload(Matter.partner_attorney),
        )
        .where(Matter.id == matter_id, Matter.tenant_id == tenant_id)
    )
    matter = result.scalar_one_or_none()
    if not matter:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


def _validate_primary_plugin(primary_plugin: str | None) -> str | None:
    """Validate a matter plugin binding. None keeps the matter general-purpose."""
    if not primary_plugin:
        return None
    plugin = primary_plugin.strip()
    if not plugin:
        return None
    if get_plugin_manifest(plugin) is None:
        raise HTTPException(status_code=400, detail="Unknown primary_plugin")
    return plugin


async def _compute_budget_utilization(
    db: AsyncSession, matter_id: uuid.UUID, tenant_id: uuid.UUID
) -> BudgetUtilization:
    """Compute budget utilization for a matter."""
    billed_result = await db.execute(
        select(
            func.coalesce(func.sum(TimeEntry.hours), 0),
            func.coalesce(func.sum(TimeEntry.amount), 0),
        ).where(
            TimeEntry.matter_id == matter_id,
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.is_billable.is_(True),
        )
    )
    total_hours, total_billed = billed_result.one()
    total_hours = float(total_hours or 0)
    total_billed = Decimal(str(total_billed or 0))

    paid_result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.tenant_id == tenant_id,
            Payment.invoice_id.in_(
                select(Invoice.id).where(
                    Invoice.matter_id == matter_id,
                    Invoice.tenant_id == tenant_id,
                )
            ),
        )
    )
    total_paid = Decimal(str(paid_result.scalar() or 0))

    unbilled_result = await db.execute(
        select(
            func.coalesce(func.sum(TimeEntry.amount), 0),
        ).where(
            TimeEntry.matter_id == matter_id,
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.is_billable.is_(True),
            TimeEntry.invoice_id.is_(None),
        )
    )
    total_unbilled = Decimal(str(unbilled_result.scalar() or 0))

    return BudgetUtilization(
        budget_amount=None,
        budget_currency="USD",
        total_hours=total_hours,
        total_billed=total_billed,
        total_paid=total_paid,
        total_unbilled=total_unbilled,
        utilization_pct=None,
        remaining=None,
    )


def _matter_to_response(
    matter: Matter, budget: BudgetUtilization | None = None
) -> MatterResponse:
    """Convert a Matter ORM instance to a MatterResponse."""
    client_name = None
    if matter.client:
        client_name = getattr(matter.client, "display_name", None)

    attorney_name = None
    if matter.attorney_of_record:
        attorney_name = getattr(matter.attorney_of_record, "full_name", None)

    partner_attorney_name = None
    if matter.partner_attorney:
        partner_attorney_name = getattr(matter.partner_attorney, "full_name", None)

    assignments = []
    for a in matter.assignments:
        user_name = a.user.full_name if a.user else "Unknown"
        assignments.append(
            MatterAssignmentResponse(
                id=str(a.id),
                user_id=str(a.user_id),
                user_name=user_name,
                role=a.role,
                is_primary=a.is_primary,
                is_active_working=a.is_active_working,
                assigned_at=a.assigned_at,
            )
        )

    return MatterResponse(
        id=str(matter.id),
        slug=matter.slug,
        matter_name=matter.matter_name,
        description=matter.description,
        matter_type=matter.matter_type,
        practice_area=matter.practice_area,
        role=matter.role,
        counterparty=matter.counterparty,
        jurisdiction=matter.jurisdiction,
        status=matter.status,
        stage=matter.stage,
        source=matter.source,
        risk_level=matter.risk_level,
        materiality=matter.materiality,
        exposure_range=matter.exposure_range,
        conflicts_status=matter.conflicts_status,
        conflicts_override_reason=matter.conflicts_override_reason,
        legal_hold_issued=matter.legal_hold_issued,
        legal_hold_details=matter.legal_hold_details,
        key_dates=matter.key_dates,
        initial_posture=matter.initial_posture,
        decision=matter.decision,
        is_closed=matter.is_closed,
        outcome=matter.outcome,
        final_cost=matter.final_cost,
        outside_counsel=matter.outside_counsel,
        court=matter.court,
        judge=matter.judge,
        case_number=matter.case_number,
        client_contact_id=(
            str(matter.client_contact_id) if matter.client_contact_id else None
        ),
        client_name=client_name,
        attorney_of_record_id=(
            str(matter.attorney_of_record_id) if matter.attorney_of_record_id else None
        ),
        attorney_of_record_name=attorney_name,
        partner_attorney_id=(
            str(matter.partner_attorney_id) if matter.partner_attorney_id else None
        ),
        partner_attorney_name=partner_attorney_name,
        retention_until=matter.retention_until,
        archived_at=matter.archived_at,
        budget_amount=matter.budget_amount,
        budget_currency=matter.budget_currency,
        budget_notification_threshold=matter.budget_notification_threshold,
        billing_cycle=matter.billing_cycle,
        billing_method=matter.billing_method,
        hourly_rate=matter.hourly_rate,
        contingency_percentage=matter.contingency_percentage,
        tax_rate=matter.tax_rate,
        assignments=assignments,
        budget_utilization=budget,
        memory_content=matter.memory_content,
        cloud_folder=matter.cloud_folder,
        primary_plugin=matter.primary_plugin,
        plugin_workflow_state=matter.plugin_workflow_state,
        created_at=matter.created_at,
        updated_at=matter.updated_at,
    )


# ── Core CRUD ─────────────────────────────────────────────────────────────────


@router.get("", response_model=MatterListResponse)
async def list_matters(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status: str | None = Query(None),
    matter_type: str | None = Query(None),
    practice_area: str | None = Query(None),
    risk_level: str | None = Query(None),
    assigned_to: str | None = Query(None),
    client_id: str | None = Query(None),
    search: str | None = Query(None),
    sort_by: str = Query("updated_at"),
    sort_dir: str = Query("desc"),
):
    """List matters with filters, search, and pagination."""
    user = await get_current_user(request, db)
    tenant_id = user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    conditions = [Matter.tenant_id == tenant_id]

    if status:
        conditions.append(Matter.status == status)
    if matter_type:
        conditions.append(Matter.matter_type == matter_type)
    if practice_area:
        conditions.append(Matter.practice_area == practice_area)
    if risk_level:
        conditions.append(Matter.risk_level == risk_level)
    if client_id:
        conditions.append(Matter.client_contact_id == uuid.UUID(client_id))
    if search:
        conditions.append(Matter.matter_name.ilike(f"%{search}%"))
    if assigned_to:
        conditions.append(
            Matter.id.in_(
                select(MatterAssignment.matter_id).where(
                    MatterAssignment.user_id == uuid.UUID(assigned_to),
                    MatterAssignment.tenant_id == tenant_id,
                )
            )
        )

    # Count
    count_q = select(func.count()).select_from(Matter).where(and_(*conditions))
    total = (await db.execute(count_q)).scalar() or 0

    # Fetch
    sort_col = getattr(Matter, sort_by, Matter.updated_at)
    if sort_dir == "asc":
        sort_col = sort_col.asc()
    else:
        sort_col = sort_col.desc()

    q = (
        select(Matter)
        .options(
            selectinload(Matter.assignments).selectinload(MatterAssignment.user),
            selectinload(Matter.client),
            selectinload(Matter.attorney_of_record),
        )
        .where(and_(*conditions))
        .order_by(sort_col)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(q)
    matters = result.unique().scalars().all()

    # Preload billed totals in one query (avoid N+1)
    matter_ids = [m.id for m in matters]
    billed_map = {}
    if matter_ids:
        billed_rows = (
            await db.execute(
                select(
                    TimeEntry.matter_id,
                    func.coalesce(func.sum(TimeEntry.amount), 0),
                )
                .where(
                    TimeEntry.matter_id.in_(matter_ids),
                    TimeEntry.tenant_id == tenant_id,
                    TimeEntry.is_billable.is_(True),
                )
                .group_by(TimeEntry.matter_id)
            )
        ).all()
        billed_map = {row[0]: Decimal(str(row[1])) for row in billed_rows}

    items = []
    for m in matters:
        client_name = getattr(m.client, "display_name", None) if m.client else None
        attorney_name = (
            getattr(m.attorney_of_record, "full_name", None)
            if m.attorney_of_record
            else None
        )
        assigned_to = [
            a.user.full_name for a in m.assignments if a.user and a.user.full_name
        ]

        total_billed = billed_map.get(m.id, Decimal("0"))

        util_pct = None
        if m.budget_amount and m.budget_amount > 0:
            util_pct = round(float(total_billed / m.budget_amount * 100), 1)

        # Next deadline
        next_deadline = None
        if m.key_dates and isinstance(m.key_dates, dict):
            from datetime import date as date_type

            dates = []
            for v in m.key_dates.values():
                if v:
                    try:
                        d = date_type.fromisoformat(str(v)[:10])
                        if d >= date.today():
                            dates.append(d)
                    except (ValueError, TypeError):
                        pass
            if dates:
                next_deadline = datetime.combine(
                    min(dates), datetime.min.time(), tzinfo=timezone.utc
                )

        items.append(
            MatterSummary(
                id=str(m.id),
                slug=m.slug,
                matter_name=m.matter_name,
                description=m.description,
                matter_type=m.matter_type,
                practice_area=m.practice_area,
                status=m.status,
                risk_level=m.risk_level,
                counterparty=m.counterparty,
                primary_plugin=m.primary_plugin,
                client_name=client_name,
                attorney_of_record_name=attorney_name,
                assigned_to=assigned_to,
                budget_amount=m.budget_amount,
                total_billed=total_billed,
                budget_utilization_pct=util_pct,
                is_overdue=m.status in ("active",) and bool(next_deadline),
                next_deadline=next_deadline,
                created_at=m.created_at,
            )
        )

    return MatterListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", status_code=201, response_model=MatterResponse)
async def create_matter(
    body: MatterCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MatterResponse:
    """Create a new matter with optional initial assignments."""
    user = await get_current_user(request, db)
    tenant_id = user.tenant_id

    slug = _generate_slug(body.matter_name)

    attorney_of_record_uuid = None
    if body.attorney_of_record_id:
        try:
            attorney_of_record_uuid = uuid.UUID(body.attorney_of_record_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid attorney_of_record_id")

    partner_attorney_uuid = None
    if body.partner_attorney_id:
        try:
            partner_attorney_uuid = uuid.UUID(body.partner_attorney_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid partner_attorney_id")

    # Default retention: 7 years from today
    from datetime import date as date_type, timedelta

    retention_until = body.key_dates and body.key_dates.get("retention_until")
    if not retention_until:
        retention_until = date_type.today() + timedelta(days=365 * 7)

    matter = Matter(
        tenant_id=tenant_id,
        user_id=user.id,
        slug=slug,
        matter_name=body.matter_name,
        description=body.description,
        matter_type=body.matter_type,
        role=body.role,
        counterparty=body.counterparty,
        jurisdiction=body.jurisdiction,
        source=body.source,
        practice_area=body.practice_area,
        court=body.court,
        judge=body.judge,
        case_number=body.case_number,
        billing_cycle=body.billing_cycle,
        billing_method=body.billing_method,
        hourly_rate=body.hourly_rate,
        budget_amount=body.budget_amount,
        budget_currency=body.budget_currency,
        status=body.status,
        risk_level=body.risk_level,
        stage=body.stage,
        key_dates=body.key_dates,
        initial_posture=body.initial_posture,
        client_contact_id=(
            uuid.UUID(body.client_contact_id) if body.client_contact_id else None
        ),
        attorney_of_record_id=attorney_of_record_uuid,
        partner_attorney_id=partner_attorney_uuid,
        retention_until=retention_until,
        memory_content=body.memory_content,
        primary_plugin=_validate_primary_plugin(body.primary_plugin),
        plugin_workflow_state=body.plugin_workflow_state,
    )
    db.add(matter)
    await db.flush()

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if tenant and tenant.cloud_root_folder:
        try:
            cloud_folder = await initialize_matter_folders(
                db=db,
                tenant_id=str(tenant_id),
                matter_slug=slug,
                cloud_root=tenant.cloud_root_folder,
            )
        except Exception:
            logger.warning(
                "Failed to initialize cloud folders for matter %s",
                matter.id,
                exc_info=True,
            )
            raise HTTPException(
                status_code=422,
                detail="Cloud folder provisioning failed — check cloud credentials",
            )
        if not cloud_folder:
            raise HTTPException(
                status_code=422,
                detail="Cloud folder provisioning failed — check cloud credentials",
            )
        matter.cloud_folder = cloud_folder

    # Create initial event
    event = MatterEvent(
        tenant_id=tenant_id,
        matter_id=matter.id,
        event_type="intake",
        title="Matter opened",
        content=f"Matter '{body.matter_name}' created by {user.full_name or user.email}.",
        note_type="system",
        created_by=user.id,
    )
    db.add(event)

    # Create initial assignments
    # If an explicit attorney of record is set, they become lead/primary;
    # otherwise the creator is lead/primary.
    assigned_ids = {str(user.id)}
    for uid in body.assigned_user_ids:
        assigned_ids.add(uid)
    if attorney_of_record_uuid:
        assigned_ids.add(str(attorney_of_record_uuid))

    primary_uid = (
        str(attorney_of_record_uuid) if attorney_of_record_uuid else str(user.id)
    )

    for uid in assigned_ids:
        try:
            uid_uuid = uuid.UUID(uid)
        except (ValueError, TypeError):
            continue
        is_primary = uid == primary_uid
        role = "lead_attorney" if is_primary else "associate"
        assignment = MatterAssignment(
            tenant_id=tenant_id,
            matter_id=matter.id,
            user_id=uid_uuid,
            role=role,
            is_primary=is_primary,
        )
        db.add(assignment)

    if matter.cloud_folder:
        valid_assigned_user_ids = []
        for uid in assigned_ids:
            try:
                valid_assigned_user_ids.append(uuid.UUID(uid))
            except (ValueError, TypeError):
                continue
        user_rows = await db.execute(
            select(User.email).where(
                User.tenant_id == tenant_id,
                User.id.in_(valid_assigned_user_ids),
            )
        )
        assigned_emails = [email for (email,) in user_rows.all() if email]
        await share_matter_folders(
            db=db,
            tenant_id=str(tenant_id),
            cloud_folder=matter.cloud_folder,
            user_emails=assigned_emails,
        )

    await db.commit()
    await db.refresh(matter)

    # Reload with relationships
    result = await db.execute(
        select(Matter)
        .options(
            selectinload(Matter.assignments).selectinload(MatterAssignment.user),
            selectinload(Matter.client),
            selectinload(Matter.attorney_of_record),
        )
        .where(Matter.id == matter.id)
    )
    matter = result.unique().scalar_one()

    budget = await _compute_budget_utilization(db, matter.id, tenant_id)
    budget.budget_amount = matter.budget_amount
    budget.budget_currency = matter.budget_currency or "USD"
    return _matter_to_response(matter, budget)


@router.get("/my", response_model=list[MatterSummaryMyMatters])
async def get_my_matters(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get matters assigned to the current user, sorted by deadline, role-aware."""
    user = await get_current_user(request, db)
    tenant_id = user.tenant_id

    q = (
        select(Matter)
        .options(
            selectinload(Matter.assignments).selectinload(MatterAssignment.user),
            selectinload(Matter.client),
            selectinload(Matter.attorney_of_record),
        )
        .where(
            Matter.tenant_id == tenant_id,
            Matter.is_closed.is_(False),
            Matter.id.in_(
                select(MatterAssignment.matter_id).where(
                    MatterAssignment.user_id == user.id,
                    MatterAssignment.tenant_id == tenant_id,
                )
            ),
        )
        .limit(100)
    )
    result = await db.execute(q)
    matters = result.unique().scalars().all()

    from datetime import date as date_type

    today = date_type.today()
    items = []
    for m in matters:
        client_name = getattr(m.client, "display_name", None) if m.client else None
        attorney_name = (
            getattr(m.attorney_of_record, "full_name", None)
            if m.attorney_of_record
            else None
        )
        assigned_to = [
            a.user.full_name for a in m.assignments if a.user and a.user.full_name
        ]

        # Find current user's assignment row
        my_assignment = next((a for a in m.assignments if a.user_id == user.id), None)
        my_role = my_assignment.role if my_assignment else "observer"
        my_assignment_id = str(my_assignment.id) if my_assignment else ""
        is_active_working = my_assignment.is_active_working if my_assignment else False

        # Other active workers on this matter
        active_workers = [
            a.user.full_name
            for a in m.assignments
            if a.is_active_working
            and a.user_id != user.id
            and a.user
            and a.user.full_name
        ]

        # Next deadline from key_dates
        next_deadline = None
        overdue_label = None
        if m.key_dates and isinstance(m.key_dates, dict):
            dates = []
            for v in m.key_dates.values():
                if v:
                    try:
                        d = date_type.fromisoformat(str(v)[:10])
                        dates.append(d)
                    except (ValueError, TypeError):
                        pass
            if dates:
                next_d = min(dates)
                next_deadline = datetime.combine(
                    next_d, datetime.min.time(), tzinfo=timezone.utc
                )
                delta = (next_d - today).days
                if delta < 0:
                    overdue_label = (
                        f"{abs(delta)} day{'s' if abs(delta) != 1 else ''} overdue"
                    )
                elif delta == 0:
                    overdue_label = "Due today"
                elif delta <= 7:
                    overdue_label = f"Due in {delta} day{'s' if delta != 1 else ''}"

        items.append(
            MatterSummaryMyMatters(
                id=str(m.id),
                slug=m.slug,
                matter_name=m.matter_name,
                description=m.description,
                matter_type=m.matter_type,
                practice_area=m.practice_area,
                status=m.status,
                risk_level=m.risk_level,
                counterparty=m.counterparty,
                primary_plugin=m.primary_plugin,
                client_name=client_name,
                attorney_of_record_name=attorney_name,
                assigned_to=assigned_to,
                budget_amount=m.budget_amount,
                total_billed=Decimal("0"),
                budget_utilization_pct=None,
                is_overdue=overdue_label is not None and "overdue" in overdue_label,
                next_deadline=next_deadline,
                created_at=m.created_at,
                my_role=my_role,
                my_assignment_id=my_assignment_id,
                is_active_working=is_active_working,
                active_workers=active_workers,
                overdue_deadline_label=overdue_label,
            )
        )

    # Sort by next_deadline ascending (nulls last)
    items.sort(
        key=lambda x: (
            x.next_deadline is None,
            x.next_deadline or datetime.max.replace(tzinfo=timezone.utc),
        )
    )
    return items


@router.get("/stats", response_model=MatterStats)
async def get_matter_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated matter statistics for the portfolio."""
    user = await get_current_user(request, db)
    tenant_id = user.tenant_id

    # Total
    total_q = await db.execute(
        select(func.count()).select_from(Matter).where(Matter.tenant_id == tenant_id)
    )
    total = total_q.scalar() or 0

    # By status
    status_q = await db.execute(
        select(Matter.status, func.count())
        .where(Matter.tenant_id == tenant_id)
        .group_by(Matter.status)
    )
    by_status = {row[0]: row[1] for row in status_q.all()}

    # By type
    type_q = await db.execute(
        select(Matter.matter_type, func.count())
        .where(Matter.tenant_id == tenant_id)
        .group_by(Matter.matter_type)
    )
    by_type = {row[0]: row[1] for row in type_q.all()}

    # By practice area
    area_q = await db.execute(
        select(Matter.practice_area, func.count())
        .where(Matter.tenant_id == tenant_id, Matter.practice_area.isnot(None))
        .group_by(Matter.practice_area)
    )
    by_practice_area = {row[0]: row[1] for row in area_q.all()}

    # By risk
    risk_q = await db.execute(
        select(Matter.risk_level, func.count())
        .where(Matter.tenant_id == tenant_id, Matter.risk_level.isnot(None))
        .group_by(Matter.risk_level)
    )
    by_risk = {row[0]: row[1] for row in risk_q.all()}

    # Legal holds
    holds_q = await db.execute(
        select(func.count())
        .select_from(Matter)
        .where(
            Matter.tenant_id == tenant_id,
            Matter.legal_hold_issued.is_(True),
        )
    )
    active_holds = holds_q.scalar() or 0

    # Budget totals
    budget_q = await db.execute(
        select(func.coalesce(func.sum(Matter.budget_amount), 0)).where(
            Matter.tenant_id == tenant_id
        )
    )
    total_budget = Decimal(str(budget_q.scalar() or 0))

    billed_q = await db.execute(
        select(func.coalesce(func.sum(TimeEntry.amount), 0)).where(
            TimeEntry.tenant_id == tenant_id, TimeEntry.is_billable.is_(True)
        )
    )
    total_billed = Decimal(str(billed_q.scalar() or 0))

    unbilled_q = await db.execute(
        select(func.coalesce(func.sum(TimeEntry.amount), 0)).where(
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.is_billable.is_(True),
            TimeEntry.invoice_id.is_(None),
        )
    )
    total_unbilled = Decimal(str(unbilled_q.scalar() or 0))

    return MatterStats(
        total=total,
        by_status=by_status,
        by_type=by_type,
        by_practice_area=by_practice_area,
        by_risk=by_risk,
        active_legal_holds=active_holds,
        total_budget=total_budget if total_budget > 0 else None,
        total_billed=total_billed,
        total_unbilled=total_unbilled,
    )


@router.get("/{matter_id}", response_model=MatterResponse)
async def get_matter(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get full matter detail with assignments, budget, and client info."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)
    budget = await _compute_budget_utilization(db, matter.id, user.tenant_id)
    budget.budget_amount = matter.budget_amount
    budget.budget_currency = matter.budget_currency or "USD"
    if budget.budget_amount and budget.budget_amount > 0:
        budget.utilization_pct = round(
            float(budget.total_billed / budget.budget_amount * 100), 1
        )
        budget.remaining = budget.budget_amount - budget.total_billed
    return _matter_to_response(matter, budget)


@router.patch("/{matter_id}", response_model=MatterResponse)
async def update_matter(
    matter_id: str,
    body: MatterUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update matter fields."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    update_data = body.model_dump(exclude_unset=True)

    # Handle UUID FK conversions
    if "client_contact_id" in update_data:
        cid = update_data.pop("client_contact_id")
        matter.client_contact_id = uuid.UUID(cid) if cid else None

    if "attorney_of_record_id" in update_data:
        aid = update_data.pop("attorney_of_record_id")
        matter.attorney_of_record_id = uuid.UUID(aid) if aid else None

    if "partner_attorney_id" in update_data:
        paid = update_data.pop("partner_attorney_id")
        matter.partner_attorney_id = uuid.UUID(paid) if paid else None

    if "is_archived" in update_data:
        is_archived = update_data.pop("is_archived")
        if is_archived and not matter.archived_at:
            matter.archived_at = datetime.now(timezone.utc)
        elif not is_archived:
            matter.archived_at = None

    if "primary_plugin" in update_data:
        matter.primary_plugin = _validate_primary_plugin(
            update_data.pop("primary_plugin")
        )

    for field, value in update_data.items():
        if hasattr(matter, field):
            setattr(matter, field, value)

    await db.commit()
    await db.refresh(matter)

    # Reload with relationships
    result = await db.execute(
        select(Matter)
        .options(
            selectinload(Matter.assignments).selectinload(MatterAssignment.user),
            selectinload(Matter.client),
            selectinload(Matter.attorney_of_record),
        )
        .where(Matter.id == matter.id)
    )
    matter = result.unique().scalar_one()
    budget = await _compute_budget_utilization(db, matter.id, user.tenant_id)
    budget.budget_amount = matter.budget_amount
    budget.budget_currency = matter.budget_currency or "USD"
    if budget.budget_amount and budget.budget_amount > 0:
        budget.utilization_pct = round(
            float(budget.total_billed / budget.budget_amount * 100), 1
        )
        budget.remaining = budget.budget_amount - budget.total_billed
    return _matter_to_response(matter, budget)


@router.delete("/{matter_id}", status_code=204)
async def close_matter(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Soft-close a matter (sets is_closed=True, status='closed')."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    matter.is_closed = True
    matter.status = "closed"
    await db.commit()
    return None


# ── Assignments ───────────────────────────────────────────────────────────────


@router.get("/{matter_id}/assignments", response_model=list[MatterAssignmentResponse])
async def list_assignments(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List users assigned to a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    result = await db.execute(
        select(MatterAssignment)
        .options(selectinload(MatterAssignment.user))
        .where(
            MatterAssignment.matter_id == matter.id,
            MatterAssignment.tenant_id == user.tenant_id,
        )
        .order_by(MatterAssignment.is_primary.desc(), MatterAssignment.assigned_at)
    )
    assignments = result.scalars().all()

    return [
        MatterAssignmentResponse(
            id=str(a.id),
            user_id=str(a.user_id),
            user_name=a.user.full_name if a.user else "Unknown",
            role=a.role,
            is_primary=a.is_primary,
            is_active_working=a.is_active_working,
            assigned_at=a.assigned_at,
        )
        for a in assignments
    ]


@router.post(
    "/{matter_id}/assignments", status_code=201, response_model=MatterAssignmentResponse
)
async def add_assignment(
    matter_id: str,
    body: MatterAssignmentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Assign a user to a matter."""
    current_user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, current_user.tenant_id)

    try:
        uid = uuid.UUID(body.user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid user ID")

    # Verify user exists in tenant
    user_check = await db.execute(
        select(User).where(User.id == uid, User.tenant_id == current_user.tenant_id)
    )
    if not user_check.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    assignment = MatterAssignment(
        tenant_id=current_user.tenant_id,
        matter_id=matter.id,
        user_id=uid,
        role=body.role,
        is_primary=body.is_primary,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)

    # Reload with user
    result = await db.execute(
        select(MatterAssignment)
        .options(selectinload(MatterAssignment.user))
        .where(MatterAssignment.id == assignment.id)
    )
    assignment = result.scalar_one()

    return MatterAssignmentResponse(
        id=str(assignment.id),
        user_id=str(assignment.user_id),
        user_name=assignment.user.full_name if assignment.user else "Unknown",
        role=assignment.role,
        is_primary=assignment.is_primary,
        assigned_at=assignment.assigned_at,
    )


@router.patch(
    "/{matter_id}/assignments/{assignment_id}/active",
    response_model=MatterAssignmentResponse,
)
async def set_active_working(
    matter_id: str,
    assignment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    active: bool = True,
):
    """Toggle the 'actively working' status on an assignment (paralegal status flag)."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    result = await db.execute(
        select(MatterAssignment)
        .options(selectinload(MatterAssignment.user))
        .where(
            MatterAssignment.id == assignment_id,
            MatterAssignment.matter_id == matter.id,
            MatterAssignment.tenant_id == user.tenant_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    assignment.is_active_working = active
    await db.commit()
    await db.refresh(assignment)

    return MatterAssignmentResponse(
        id=str(assignment.id),
        user_id=str(assignment.user_id),
        user_name=assignment.user.full_name if assignment.user else "Unknown",
        role=assignment.role,
        is_primary=assignment.is_primary,
        is_active_working=assignment.is_active_working,
        assigned_at=assignment.assigned_at,
    )


@router.delete("/{matter_id}/assignments/{assignment_id}", status_code=204)
async def remove_assignment(
    matter_id: str,
    assignment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Remove a user from a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    result = await db.execute(
        select(MatterAssignment).where(
            MatterAssignment.id == assignment_id,
            MatterAssignment.matter_id == matter.id,
            MatterAssignment.tenant_id == user.tenant_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    await db.delete(assignment)
    await db.commit()
    return None


# ── Notes ─────────────────────────────────────────────────────────────────────


@router.get("/{matter_id}/notes", response_model=list[MatterNoteResponse])
async def list_notes(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    note_type: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    """List notes for a matter, filterable by type."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    conditions = [
        MatterNote.matter_id == matter.id,
        MatterNote.tenant_id == user.tenant_id,
    ]
    if note_type:
        conditions.append(MatterNote.note_type == note_type)

    q = (
        select(MatterNote)
        .options(selectinload(MatterNote.author))
        .where(and_(*conditions))
        .order_by(MatterNote.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(q)
    notes = result.scalars().all()

    return [
        MatterNoteResponse(
            id=str(n.id),
            matter_id=str(n.matter_id),
            author_id=str(n.author_id) if n.author_id else None,
            author_name=n.author.full_name if n.author else None,
            note_type=n.note_type,
            title=n.title,
            content=n.content,
            is_billable=n.is_billable,
            hours=n.hours,
            created_at=n.created_at,
            updated_at=n.updated_at,
        )
        for n in notes
    ]


@router.post("/{matter_id}/notes", status_code=201, response_model=MatterNoteResponse)
async def add_note(
    matter_id: str,
    body: MatterNoteCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a note to a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    note = MatterNote(
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        author_id=user.id,
        note_type=body.note_type,
        title=body.title,
        content=body.content,
        is_billable=body.is_billable,
        hours=body.hours,
    )
    db.add(note)

    # Also create a timeline event for the note
    event = MatterEvent(
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        event_type="note",
        title=f"Note: {body.title}",
        content=body.content,
        note_type=body.note_type,
        created_by=user.id,
    )
    db.add(event)

    await db.commit()
    await db.refresh(note)

    return MatterNoteResponse(
        id=str(note.id),
        matter_id=str(note.matter_id),
        author_id=str(note.author_id) if note.author_id else None,
        author_name=user.full_name,
        note_type=note.note_type,
        title=note.title,
        content=note.content,
        is_billable=note.is_billable,
        hours=note.hours,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.patch("/{matter_id}/notes/{note_id}", response_model=MatterNoteResponse)
async def update_note(
    matter_id: str,
    note_id: str,
    body: MatterNoteUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update a note."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    result = await db.execute(
        select(MatterNote).where(
            MatterNote.id == note_id,
            MatterNote.matter_id == matter.id,
            MatterNote.tenant_id == user.tenant_id,
        )
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if hasattr(note, field):
            setattr(note, field, value)

    await db.commit()
    await db.refresh(note)

    return MatterNoteResponse(
        id=str(note.id),
        matter_id=str(note.matter_id),
        author_id=str(note.author_id) if note.author_id else None,
        author_name=note.author.full_name if note.author else None,
        note_type=note.note_type,
        title=note.title,
        content=note.content,
        is_billable=note.is_billable,
        hours=note.hours,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.delete("/{matter_id}/notes/{note_id}", status_code=204)
async def delete_note(
    matter_id: str,
    note_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a note."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    result = await db.execute(
        select(MatterNote).where(
            MatterNote.id == note_id,
            MatterNote.matter_id == matter.id,
            MatterNote.tenant_id == user.tenant_id,
        )
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    await db.delete(note)
    await db.commit()
    return None


# ── Timeline ──────────────────────────────────────────────────────────────────


@router.get("/{matter_id}/timeline", response_model=list[TimelineEntry])
async def get_timeline(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    entry_types: str | None = Query(None),
    limit: int = Query(100, le=500),
):
    """Get unified timeline feed for a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    entries = []

    # MatterEvents
    if not entry_types or "event" in entry_types:
        event_q = await db.execute(
            select(MatterEvent)
            .where(MatterEvent.matter_id == matter.id)
            .order_by(MatterEvent.created_at.desc())
            .limit(limit)
        )
        for e in event_q.scalars().all():
            entries.append(
                TimelineEntry(
                    entry_type="event",
                    id=str(e.id),
                    title=e.title,
                    content=e.content,
                    created_by=str(e.created_by),
                    created_by_name=None,
                    created_at=e.created_at,
                    metadata={"event_type": e.event_type, "note_type": e.note_type},
                )
            )

    # MatterNotes
    if not entry_types or "note" in entry_types:
        note_q = await db.execute(
            select(MatterNote)
            .options(selectinload(MatterNote.author))
            .where(MatterNote.matter_id == matter.id)
            .order_by(MatterNote.created_at.desc())
            .limit(limit)
        )
        for n in note_q.scalars().all():
            entries.append(
                TimelineEntry(
                    entry_type="note",
                    id=str(n.id),
                    title=n.title,
                    content=n.content,
                    created_by=str(n.author_id) if n.author_id else None,
                    created_by_name=n.author.full_name if n.author else None,
                    created_at=n.created_at,
                    metadata={
                        "note_type": n.note_type,
                        "is_billable": n.is_billable,
                        "hours": str(n.hours) if n.hours else None,
                    },
                )
            )

    # Sort combined entries by created_at descending
    entries.sort(key=lambda x: x.created_at, reverse=True)
    return entries[:limit]


# ── Budget ────────────────────────────────────────────────────────────────────


@router.get("/{matter_id}/budget", response_model=BudgetUtilization)
async def get_budget(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get budget utilization for a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    budget = await _compute_budget_utilization(db, matter.id, user.tenant_id)
    budget.budget_amount = matter.budget_amount
    budget.budget_currency = matter.budget_currency or "USD"
    if budget.budget_amount and budget.budget_amount > 0:
        budget.utilization_pct = round(
            float(budget.total_billed / budget.budget_amount * 100), 1
        )
        budget.remaining = budget.budget_amount - budget.total_billed
    return budget


@router.patch("/{matter_id}/budget", response_model=MatterResponse)
async def update_budget(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    budget_amount: Decimal | None = None,
    budget_currency: str | None = None,
    budget_notification_threshold: Decimal | None = None,
):
    """Update budget fields for a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    if budget_amount is not None:
        matter.budget_amount = budget_amount
    if budget_currency is not None:
        matter.budget_currency = budget_currency
    if budget_notification_threshold is not None:
        matter.budget_notification_threshold = budget_notification_threshold

    await db.commit()

    result = await db.execute(
        select(Matter)
        .options(
            selectinload(Matter.assignments).selectinload(MatterAssignment.user),
            selectinload(Matter.client),
            selectinload(Matter.attorney_of_record),
        )
        .where(Matter.id == matter.id)
    )
    matter = result.unique().scalar_one()
    budget = await _compute_budget_utilization(db, matter.id, user.tenant_id)
    budget.budget_amount = matter.budget_amount
    budget.budget_currency = matter.budget_currency or "USD"
    if budget.budget_amount and budget.budget_amount > 0:
        budget.utilization_pct = round(
            float(budget.total_billed / budget.budget_amount * 100), 1
        )
        budget.remaining = budget.budget_amount - budget.total_billed
    return _matter_to_response(matter, budget)


# ── Retainers ─────────────────────────────────────────────────────────────────


@router.get("/{matter_id}/retainers", response_model=list[RetainerResponse])
async def list_retainers(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List retainers for a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    result = await db.execute(
        select(Retainer)
        .options(
            selectinload(Retainer.contact),
            selectinload(Retainer.transactions),
        )
        .where(
            Retainer.matter_id == matter.id,
            Retainer.tenant_id == user.tenant_id,
        )
        .order_by(Retainer.created_at.desc())
    )
    retainers = result.unique().scalars().all()

    return [
        RetainerResponse(
            id=str(r.id),
            matter_id=str(r.matter_id),
            contact_id=str(r.contact_id),
            contact_name=r.contact.display_name if r.contact else None,
            retainer_type=r.retainer_type,
            amount=r.amount,
            current_balance=r.current_balance,
            minimum_balance=r.minimum_balance,
            status=r.status,
            transactions=[
                RetainerTransactionResponse(
                    id=str(t.id),
                    transaction_type=t.transaction_type,
                    amount=t.amount,
                    invoice_id=str(t.invoice_id) if t.invoice_id else None,
                    description=t.description,
                    created_by=str(t.created_by),
                    created_at=t.created_at,
                )
                for t in r.transactions
            ],
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in retainers
    ]


@router.post("/{matter_id}/retainers", status_code=201, response_model=RetainerResponse)
async def create_retainer(
    matter_id: str,
    body: RetainerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a retainer for a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    # Verify contact
    contact_check = await db.execute(
        select(Contact).where(
            Contact.id == body.contact_id,
            Contact.tenant_id == user.tenant_id,
        )
    )
    contact = contact_check.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    retainer = Retainer(
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        contact_id=uuid.UUID(body.contact_id),
        retainer_type=body.retainer_type,
        amount=body.amount,
        current_balance=body.amount,  # starts at full amount
        minimum_balance=body.minimum_balance,
        status="active",
    )
    db.add(retainer)

    # Initial deposit transaction
    tx = RetainerTransaction(
        tenant_id=user.tenant_id,
        retainer_id=retainer.id,
        transaction_type="deposit",
        amount=body.amount,
        description=f"Initial retainer deposit of ${body.amount}",
        created_by=user.id,
    )
    db.add(tx)

    await db.commit()
    await db.refresh(retainer)
    await db.refresh(tx)

    return RetainerResponse(
        id=str(retainer.id),
        matter_id=str(retainer.matter_id),
        contact_id=str(retainer.contact_id),
        contact_name=contact.display_name,
        retainer_type=retainer.retainer_type,
        amount=retainer.amount,
        current_balance=retainer.current_balance,
        minimum_balance=retainer.minimum_balance,
        status=retainer.status,
        transactions=[
            RetainerTransactionResponse(
                id=str(tx.id),
                transaction_type=tx.transaction_type,
                amount=tx.amount,
                invoice_id=None,
                description=tx.description,
                created_by=str(tx.created_by),
                created_at=tx.created_at,
            )
        ],
        created_at=retainer.created_at,
        updated_at=retainer.updated_at,
    )


@router.post(
    "/{matter_id}/retainers/{retainer_id}/drawdown", response_model=RetainerResponse
)
async def drawdown_retainer(
    matter_id: str,
    retainer_id: str,
    body: RetainerDrawdownRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Draw down from a retainer (typically when generating an invoice)."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    result = await db.execute(
        select(Retainer)
        .options(
            selectinload(Retainer.contact),
            selectinload(Retainer.transactions),
        )
        .where(
            Retainer.id == retainer_id,
            Retainer.matter_id == matter.id,
            Retainer.tenant_id == user.tenant_id,
        )
    )
    retainer = result.unique().scalar_one_or_none()
    if not retainer:
        raise HTTPException(status_code=404, detail="Retainer not found")

    if retainer.status != "active":
        raise HTTPException(status_code=400, detail="Retainer is not active")

    if body.amount > retainer.current_balance:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient retainer balance. Available: ${retainer.current_balance}",
        )

    new_balance = retainer.current_balance - body.amount
    retainer.current_balance = new_balance

    if new_balance <= 0:
        retainer.status = "depleted"

    tx = RetainerTransaction(
        tenant_id=user.tenant_id,
        retainer_id=retainer.id,
        transaction_type="drawdown",
        amount=-body.amount,  # negative = money out
        invoice_id=uuid.UUID(body.invoice_id) if body.invoice_id else None,
        description=body.description or f"Drawdown of ${body.amount}",
        created_by=user.id,
    )
    db.add(tx)

    await db.commit()
    await db.refresh(retainer)

    return RetainerResponse(
        id=str(retainer.id),
        matter_id=str(retainer.matter_id),
        contact_id=str(retainer.contact_id),
        contact_name=retainer.contact.display_name if retainer.contact else None,
        retainer_type=retainer.retainer_type,
        amount=retainer.amount,
        current_balance=retainer.current_balance,
        minimum_balance=retainer.minimum_balance,
        status=retainer.status,
        transactions=[
            RetainerTransactionResponse(
                id=str(t.id),
                transaction_type=t.transaction_type,
                amount=t.amount,
                invoice_id=str(t.invoice_id) if t.invoice_id else None,
                description=t.description,
                created_by=str(t.created_by),
                created_at=t.created_at,
            )
            for t in retainer.transactions
        ],
        created_at=retainer.created_at,
        updated_at=retainer.updated_at,
    )


# ── Time Entries (matter-scoped) ──────────────────────────────────────────────


@router.get("/{matter_id}/time-entries")
async def get_matter_time_entries(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    """List time entries for a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    q = (
        select(TimeEntry)
        .where(
            TimeEntry.matter_id == matter.id,
            TimeEntry.tenant_id == user.tenant_id,
        )
        .order_by(TimeEntry.date.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(q)
    entries = result.scalars().all()

    return [
        {
            "id": str(e.id),
            "matter_id": str(e.matter_id),
            "user_id": str(e.user_id),
            "description": e.description,
            "hours": float(e.hours),
            "hourly_rate": float(e.hourly_rate),
            "amount": float(e.amount),
            "date": str(e.date),
            "is_billable": e.is_billable,
            "status": e.status,
            "invoice_id": str(e.invoice_id) if e.invoice_id else None,
        }
        for e in entries
    ]


# ── Invoices (matter-scoped) ──────────────────────────────────────────────────


@router.get("/{matter_id}/invoices")
async def get_matter_invoices(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    """List invoices for a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    q = (
        select(Invoice)
        .options(
            selectinload(Invoice.line_items),
            selectinload(Invoice.payments),
        )
        .where(
            Invoice.matter_id == matter.id,
            Invoice.tenant_id == user.tenant_id,
        )
        .order_by(Invoice.issue_date.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(q)
    invoices = result.unique().scalars().all()

    return [
        {
            "id": str(i.id),
            "invoice_number": i.invoice_number,
            "status": i.status,
            "issue_date": str(i.issue_date),
            "due_date": str(i.due_date),
            "subtotal": float(i.subtotal),
            "tax_amount": float(i.tax_amount),
            "total": float(i.total),
            "retainer_id": str(i.retainer_id) if i.retainer_id else None,
            "billing_period_start": (
                str(i.billing_period_start) if i.billing_period_start else None
            ),
            "billing_period_end": (
                str(i.billing_period_end) if i.billing_period_end else None
            ),
            "line_items": [
                {
                    "id": str(li.id),
                    "source_type": li.source_type,
                    "description": li.description,
                    "quantity": float(li.quantity),
                    "unit_price": float(li.unit_price),
                    "amount": float(li.amount),
                }
                for li in i.line_items
            ],
            "payments": [
                {
                    "id": str(p.id),
                    "amount": float(p.amount),
                    "payment_date": str(p.payment_date),
                    "method": p.method,
                }
                for p in i.payments
            ],
        }
        for i in invoices
    ]


# ── Memory ────────────────────────────────────────────────────────────────────


@router.get("/{matter_id}/memory", response_model=MatterMemoryResponse)
async def get_matter_memory(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get the AI memory document for a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)
    return MatterMemoryResponse(
        matter_id=str(matter.id),
        memory_content=matter.memory_content,
    )


@router.put("/{matter_id}/memory", response_model=MatterMemoryResponse)
async def update_matter_memory(
    matter_id: str,
    body: MatterMemoryUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update the AI memory document for a matter."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)
    matter.memory_content = body.content
    await db.commit()
    return MatterMemoryResponse(
        matter_id=str(matter.id),
        memory_content=matter.memory_content,
    )


# ── Dashboard Summary ────────────────────────────────────────────────────────


@router.get("/{matter_id}/dashboard-summary")
async def get_matter_dashboard_summary(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return aggregated dashboard stats for a matter in one request."""
    from datetime import timedelta

    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    today = date.today()
    thirty_days = today + timedelta(days=30)

    # Task stats
    task_result = await db.execute(
        select(Task).where(
            Task.tenant_id == user.tenant_id,
            Task.matter_id == matter.id,
            Task.status.notin_(["completed", "cancelled"]),
        )
    )
    tasks = task_result.scalars().all()
    open_tasks = len(tasks)
    overdue_tasks = sum(1 for t in tasks if t.due_date and t.due_date < today)
    next_deadline = None
    upcoming = sorted(
        [
            t
            for t in tasks
            if t.due_date and t.due_date >= today and t.due_date <= thirty_days
        ],
        key=lambda t: t.due_date,
    )
    if upcoming:
        t = upcoming[0]
        next_deadline = {
            "id": str(t.id),
            "title": t.title,
            "task_type": t.task_type,
            "due_date": str(t.due_date),
            "priority": t.priority,
        }

    # Budget
    budget = await _compute_budget_utilization(db, matter.id, user.tenant_id)
    if matter.budget_amount:
        budget.budget_amount = matter.budget_amount
        budget.budget_currency = matter.budget_currency or "USD"
        if matter.budget_amount > 0:
            pct = int((float(budget.total_billed) / float(matter.budget_amount)) * 100)
            budget.utilization_pct = min(pct, 100)
            budget.remaining = matter.budget_amount - budget.total_billed

    # Last activity
    event_result = await db.execute(
        select(MatterEvent.created_at)
        .where(MatterEvent.matter_id == matter.id)
        .order_by(MatterEvent.created_at.desc())
        .limit(1)
    )
    last_event_at = event_result.scalar_one_or_none()

    # Active workers
    active_workers = [
        a.user.full_name for a in matter.assignments if a.is_active_working and a.user
    ]

    return {
        "open_tasks": open_tasks,
        "overdue_tasks": overdue_tasks,
        "next_deadline": next_deadline,
        "budget_amount": float(budget.budget_amount) if budget.budget_amount else None,
        "budget_currency": budget.budget_currency,
        "total_billed": float(budget.total_billed),
        "utilization_pct": budget.utilization_pct,
        "last_activity_at": last_event_at.isoformat() if last_event_at else None,
        "active_workers": active_workers,
    }


# ── Email Client ──────────────────────────────────────────────────────────────


@router.post("/{matter_id}/email-client")
async def email_matter_client(
    matter_id: str,
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send an email to the matter's client and log it as a communication."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)

    subject = body.get("subject", "").strip()
    email_body = body.get("body", "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="subject is required")

    # Resolve recipient email
    to_email = body.get("to_email", "").strip()
    if not to_email and matter.client:
        to_email = getattr(matter.client, "email", None) or ""
    if not to_email:
        raise HTTPException(
            status_code=422,
            detail="No client email on file. Provide to_email in the request body.",
        )

    # Build simple HTML body
    html_body = f"""
    <div style="font-family:Arial,sans-serif;font-size:14px;color:#333;line-height:1.6;">
      <p>{email_body.replace(chr(10), "<br>")}</p>
      <hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">
      <p style="font-size:12px;color:#999;">
        Re: {matter.matter_name}
        {(" — " + matter.case_number) if matter.case_number else ""}
      </p>
    </div>
    """

    svc = EmailService()
    sent = await svc.send_email(
        to=[to_email],
        subject=subject,
        html_body=html_body,
        text_body=email_body,
    )

    # Log regardless of send outcome (outbound attempt is recorded)
    log = CommunicationLog(
        tenant_id=user.tenant_id,
        direction="outbound",
        channel="email",
        status="sent" if sent else "logged",
        subject=subject,
        body=email_body,
        matter_id=matter.id,
        contact_id=matter.client_contact_id,
        created_by_user_id=user.id,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    return {
        "id": str(log.id),
        "sent": sent,
        "to": to_email,
        "subject": subject,
        "matter_id": str(matter.id),
        "logged_at": log.occurred_at.isoformat(),
    }


# ── Cloud Files ──────────────────────────────────────────────────────────────


@router.get("/{matter_id}/cloud-files")
async def get_matter_cloud_files(
    matter_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search cloud integrations for files related to this matter by name."""
    tenant_id = str(current_user.tenant_id)
    await set_tenant_context(db, tenant_id)

    matter = await _get_matter_or_404(db, matter_id, current_user.tenant_id)

    # Check if tenant has any active cloud credentials
    cred_result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == current_user.tenant_id,
            TenantCredential.is_active.is_(True),
        )
    )
    creds = cred_result.scalars().all()

    if not creds:
        return {"files": [], "connected": False}

    # Search using matter name as keywords
    keywords = [w for w in matter.matter_name.split() if len(w) > 2][:6]
    if matter.case_number:
        keywords.insert(0, matter.case_number)

    plan = {
        "keywords": keywords,
        "max_hits": 20,
        "date_after": "",
        "sources": None,
    }

    try:
        hits = await _cloud_search.search(
            db=db,
            plan=plan,
            tenant_id=tenant_id,
            user_id=str(current_user.id),
        )
    except Exception:
        return {"files": [], "connected": True}

    return {
        "connected": True,
        "files": [
            {
                "id": h.object_id,
                "title": h.title,
                "snippet": h.snippet,
                "url": h.url,
                "source": h.source,
                "provider": h.provider,
                "mime_type": h.mime_type,
                "modified_time": h.modified_time,
            }
            for h in hits
        ],
    }


# ── Cloud folder endpoints ───────────────────────────────────────────────────


@router.get("/{matter_id}/cloud-folder", response_model=MatterCloudFolderStatus)
async def get_matter_cloud_folder(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return current cloud folder provisioning status for a matter."""
    current_user = await get_current_user(request, db)
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == tenant_id,
        )
    )
    matter = result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    if matter.cloud_folder:
        return MatterCloudFolderStatus(
            status="provisioned",
            providers=matter.cloud_folder,
        )
    return MatterCloudFolderStatus(status="not_provisioned", providers={})


@router.post(
    "/{matter_id}/cloud-folder/provision", response_model=MatterCloudFolderStatus
)
async def provision_matter_cloud_folder(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """(Re-)provision cloud folders for a matter and return updated status."""
    current_user = await get_current_user(request, db)
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    matter_result = await db.execute(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == tenant_id,
        )
    )
    matter = matter_result.scalar_one_or_none()
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant or not tenant.cloud_root_folder:
        raise HTTPException(
            status_code=422,
            detail="No cloud credentials configured for this tenant",
        )

    try:
        cloud_folder = await initialize_matter_folders(
            db=db,
            tenant_id=str(tenant_id),
            matter_slug=matter.slug,
            cloud_root=tenant.cloud_root_folder,
        )
    except Exception as exc:
        logger.warning(
            "Cloud folder provision failed for matter %s: %s", matter_id, exc
        )
        raise HTTPException(
            status_code=422,
            detail="Cloud folder provisioning failed — check cloud credentials",
        )

    if not cloud_folder:
        raise HTTPException(
            status_code=422,
            detail="Cloud folder provisioning returned empty result",
        )

    matter.cloud_folder = cloud_folder

    # Share with current assignees
    user_rows = await db.execute(
        select(User.email).where(
            User.tenant_id == tenant_id,
            User.id.in_(
                select(MatterAssignment.user_id).where(
                    MatterAssignment.matter_id == matter.id
                )
            ),
        )
    )
    assigned_emails = [email for (email,) in user_rows.all() if email]
    await share_matter_folders(
        db=db,
        tenant_id=str(tenant_id),
        cloud_folder=cloud_folder,
        user_emails=assigned_emails,
    )

    await db.commit()
    await db.refresh(matter)

    return MatterCloudFolderStatus(
        status="provisioned",
        providers=matter.cloud_folder,
    )


# ── Slug generation ───────────────────────────────────────────────────────────


def _generate_slug(name: str) -> str:
    """Generate a URL-safe slug from a matter name."""
    import re

    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = slug[:100]  # keep it reasonable
    # Append a short UUID suffix for uniqueness
    suffix = str(uuid.uuid4())[:8]
    return f"{slug}-{suffix}"
