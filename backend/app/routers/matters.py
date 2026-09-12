"""Matter/case management router — firm-wide matters with assignments, notes, retainers, billing."""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import async_session_maker, get_db, set_tenant_context
from app.middleware.tenant import get_current_user, require_admin
from app.models.billing import TimeEntry, Expense, Invoice, Payment
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact
from app.models.matter_assignment import MatterAssignment
from app.models.matter_note import MatterNote
from app.models.plugin import Matter, MatterEvent
from app.models.retainer import Retainer, RetainerTransaction
from app.services.matter_closing import close_readiness
from app.models.task import Task
from app.models.user import User
from app.models.tenant import Tenant
from app.services.email import (
    EmailService,
    EmailDeliveryResult,
    email_delivery_http_error,
)
from app.services.connected_mail import send_client_email, DELIVERY_OUTCOME_UNKNOWN
from app.services.cloud_init import (
    ROOT_FOLDER_NAME,
    canonical_matter_folder_name,
    build_matter_folder_metadata,
    cloud_root_binding_repair_needed,
    ensure_matter_marker,
    initialize_cloud_root_folder,
    initialize_matter_folders,
    rename_cloud_folder,
    resolve_cloud_folder_reference,
    share_matter_folders,
)
from app.schemas.matter import (
    BudgetUtilization,
    MatterAssignmentCreate,
    MatterAssignmentResponse,
    MatterCloudContextFolderRequest,
    MatterCloudFolderRemapRequest,
    MatterCloudFolderRenameRequest,
    MatterCloudFolderStatus,
    MatterCreate,
    MatterFieldOptions,
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
    ResolvedPractice,
    TimelineEntry,
)
from app.services.plugins.manifest import get_plugin_manifest
from app.models.tenant_credential import TenantCredential
from app.services.practice_resolution import DEFAULT_PRACTICE, resolve_practice
from app.services.cloud_search import CloudSearchService
from app.services.cloud_sync import CloudSyncService
from app.services.cache import ExpertiseCacheManager
from app.services.matter_budget import (
    expense_client_amount_expression,
    load_matter_billable_totals,
)
from app.services.task_notifications import remove_task_from_calendars_now
from app.services.task_visibility import task_is_sms_expression
from app.services.durable_workflow_automations import enqueue_matter_event
from app.services.matter_number import (
    assign_matter_number,
    normalize_matter_number,
)

settings = get_settings()
_cloud_search = CloudSearchService()
_cloud_sync = CloudSyncService()
matter_context_cache_manager = ExpertiseCacheManager()

router = APIRouter(prefix="/api/matters", tags=["matters"])
logger = logging.getLogger(__name__)
SUPPORTED_CLOUD_FOLDER_PROVIDERS = {"onedrive", "google_drive", "sharepoint"}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _matter_detail_query():
    """Base matter select with the relationships every detail response reads."""
    return select(Matter).options(
        selectinload(Matter.assignments).selectinload(MatterAssignment.user),
        selectinload(Matter.client),
        selectinload(Matter.attorney_of_record),
        selectinload(Matter.partner_attorney),
    )


async def _get_matter_or_404(
    db: AsyncSession, matter_id: str, tenant_id: uuid.UUID
) -> Matter:
    """Fetch a matter by ID, verifying tenant ownership, or raise 404."""
    try:
        matter_uuid = uuid.UUID(str(matter_id))
    except (ValueError, AttributeError, TypeError):
        # A path segment that is not a UUID is a miss, not a server error.
        # Without this the comparison reaches Postgres and fails casting.
        raise HTTPException(status_code=404, detail="Matter not found")
    result = await db.execute(
        _matter_detail_query().where(
            Matter.id == matter_uuid, Matter.tenant_id == tenant_id
        )
    )
    matter = result.scalar_one_or_none()
    if not matter:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


async def _get_matter_by_number_or_404(
    db: AsyncSession, matter_number: str, tenant_id: uuid.UUID
) -> Matter:
    """Fetch a matter by its human-readable number within the tenant."""
    normalized = normalize_matter_number(matter_number)
    if not normalized:
        raise HTTPException(status_code=404, detail="Matter not found")
    result = await db.execute(
        _matter_detail_query().where(
            Matter.matter_number == normalized, Matter.tenant_id == tenant_id
        )
    )
    matter = result.scalar_one_or_none()
    if not matter:
        raise HTTPException(status_code=404, detail="Matter not found")
    return matter


async def _invalidate_matter_context_cache(
    tenant_id: uuid.UUID, matter_id: uuid.UUID
) -> None:
    """Best-effort invalidation after a committed context-affecting mutation."""
    try:
        if (
            matter_context_cache_manager.cache_enabled
            and not matter_context_cache_manager.redis_client
        ):
            await matter_context_cache_manager.init()
        await matter_context_cache_manager.invalidate_matter_context(
            str(matter_id), str(tenant_id)
        )
    except Exception:
        logger.warning(
            "Matter context cache invalidation failed for matter %s",
            matter_id,
            exc_info=True,
        )


async def _share_matter_with_assignees(
    db: AsyncSession, tenant_id: uuid.UUID, matter: Matter
) -> None:
    """Best-effort cloud folder sharing for users assigned to this matter."""
    if not matter.cloud_folder:
        return

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
    if not assigned_emails:
        return

    try:
        await share_matter_folders(
            db=db,
            tenant_id=str(tenant_id),
            cloud_folder=matter.cloud_folder,
            user_emails=assigned_emails,
        )
    except Exception:
        logger.warning(
            "Failed to share cloud folders for matter %s",
            matter.id,
            exc_info=True,
        )


async def _build_matter_cloud_files_response(
    db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, matter: Matter
) -> dict:
    """Return live cloud files scoped to the provisioned matter folder when possible."""
    tenant_id_str = str(tenant_id)

    cred_result = await db.execute(
        select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
            TenantCredential.is_active.is_(True),
        )
    )
    creds = cred_result.scalars().all()
    if not creds:
        return {"files": [], "connected": False}

    keywords = [w for w in (matter.matter_name or "").split() if len(w) > 2][:6]
    if matter.case_number:
        keywords.insert(0, matter.case_number)

    plan = {
        "keywords": keywords,
        "max_hits": 20,
        "date_after": "",
        "sources": ["drive", "onedrive", "sharepoint"],
    }

    try:
        hits = await _cloud_search.search(
            db=db,
            plan=plan,
            tenant_id=tenant_id_str,
            user_id=str(user_id),
            matter_cloud_folder=matter.cloud_folder,
            # The matter page waits on this panel, so a partial list now beats
            # a complete one after the whole page has stalled behind it.
            budget_seconds=settings.CLOUD_SEARCH_UI_BUDGET_SECONDS,
        )
    except Exception:
        logger.warning(
            "Matter cloud file search failed for matter %s", matter.id, exc_info=True
        )
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


def _matter_slug(matter: Matter) -> str:
    return matter.slug or _generate_slug(matter.matter_name or str(matter.id))


def _parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _matter_type(value: str | None) -> str:
    matter_type = (value or "").strip()
    return matter_type or "general"


def _cloud_folder_status(status: str, message: str = "") -> dict:
    """Return a metadata sub-object for the cloud_folder JSONB column."""
    from datetime import datetime, timezone

    return {
        "_status": status,
        "_status_message": message,
        "_status_at": datetime.now(timezone.utc).isoformat(),
    }


async def _provision_cloud_folders(
    matter_id: str,
    tenant_id: str,
    slug: str,
    cloud_root: str,
    matter_name: str = "",
    matter_number: str | None = None,
) -> None:
    """Fire-and-forget: provision cloud folders for a newly created matter.

    Runs in a separate DB session so it does not block the create-matter response.
    Writes ``_status`` metadata into ``matter.cloud_folder`` so the UI can surface
    provisioning failures.
    """
    try:
        async with async_session_maker() as db:
            await set_tenant_context(db, tenant_id)
            cloud_folder = await initialize_matter_folders(
                db=db,
                tenant_id=tenant_id,
                matter_slug=slug,
                cloud_root=cloud_root,
                folder_name=matter_name,
                matter_id=matter_id,
                matter_number=matter_number,
            )
            if not cloud_folder:
                raise RuntimeError(
                    "No cloud provider could provision this matter; reconnect and retry"
                )
            if cloud_folder:
                cloud_folder.update(_cloud_folder_status("provisioned"))
                result = await db.execute(select(Matter).where(Matter.id == matter_id))
                matter = result.scalar_one_or_none()
                if matter:
                    matter.cloud_folder = {
                        **(matter.cloud_folder or {}),
                        **cloud_folder,
                    }
                    await _share_matter_with_assignees(db, uuid.UUID(tenant_id), matter)
                    await db.commit()
    except Exception as exc:
        logger.warning(
            "Background cloud folder provisioning failed for matter %s",
            matter_id,
            exc_info=True,
        )
        # Write failure status so the UI can surface it on the next request.
        try:
            async with async_session_maker() as db:
                await set_tenant_context(db, tenant_id)
                result = await db.execute(select(Matter).where(Matter.id == matter_id))
                matter = result.scalar_one_or_none()
                if matter:
                    cf = matter.cloud_folder or {}
                    cf.update(_cloud_folder_status("failed", str(exc)[:500]))
                    matter.cloud_folder = cf
                    await db.commit()
        except Exception:
            pass


async def _compute_budget_utilization(
    db: AsyncSession, matter_id: uuid.UUID, tenant_id: uuid.UUID
) -> BudgetUtilization:
    """Compute budget utilization for a matter."""
    totals = await load_matter_billable_totals(db, matter_id, tenant_id)

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

    return BudgetUtilization(
        budget_amount=None,
        budget_currency="USD",
        total_hours=totals.total_hours,
        total_billed=totals.total_amount,
        total_paid=total_paid,
        total_unbilled=totals.total_unbilled,
        utilization_pct=None,
        remaining=None,
        billable_time_amount=totals.time_amount,
        billable_expense_amount=totals.expense_amount,
    )


def _matter_to_response(
    matter: Matter, budget: BudgetUtilization | None = None
) -> MatterResponse:
    """Convert a Matter ORM instance to a MatterResponse."""
    client_name = None
    client_email = None
    client_contact_type = None
    if matter.client:
        client_name = getattr(matter.client, "display_name", None)
        client_email = getattr(matter.client, "email", None)
        client_contact_type = getattr(matter.client, "contact_type", None)

    attorney_name = None
    if matter.attorney_of_record:
        attorney_name = getattr(matter.attorney_of_record, "full_name", None)

    partner_attorney_name = None
    if matter.partner_attorney:
        partner_attorney_name = getattr(matter.partner_attorney, "full_name", None)

    assignments = []
    fallback_at = matter.created_at or datetime.now(timezone.utc)
    for a in matter.assignments:
        user_name = a.user.full_name if a.user else "Unknown"
        assignments.append(
            MatterAssignmentResponse(
                id=str(a.id),
                user_id=str(a.user_id),
                user_name=user_name,
                role=a.role or "associate",
                is_primary=bool(a.is_primary),
                is_active_working=bool(a.is_active_working),
                assigned_at=a.assigned_at or fallback_at,
            )
        )

    updated_at = matter.updated_at or matter.created_at or datetime.now(timezone.utc)

    resolved = resolve_practice(matter.matter_type, matter.practice_area)
    resolved_practice = None
    if resolved is not DEFAULT_PRACTICE:
        resolved_practice = ResolvedPractice(slug=resolved.slug, label=resolved.label)

    return MatterResponse(
        id=str(matter.id),
        slug=_matter_slug(matter),
        matter_number=matter.matter_number,
        matter_name=matter.matter_name or "Untitled matter",
        description=matter.description,
        matter_type=matter.matter_type,
        practice_area=matter.practice_area,
        resolved_practice=resolved_practice,
        role=matter.role,
        counterparty=matter.counterparty,
        jurisdiction=matter.jurisdiction,
        venue=matter.venue,
        status=matter.status or "open",
        stage=matter.stage,
        source=matter.source,
        risk_level=matter.risk_level,
        materiality=matter.materiality,
        exposure_range=matter.exposure_range,
        conflicts_status=matter.conflicts_status or "not-run",
        conflicts_override_reason=matter.conflicts_override_reason,
        legal_hold_issued=bool(matter.legal_hold_issued),
        legal_hold_details=matter.legal_hold_details,
        key_dates=matter.key_dates,
        initial_posture=matter.initial_posture,
        decision=matter.decision,
        is_closed=bool(matter.is_closed),
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
        client_email=client_email,
        client_contact_type=client_contact_type,
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
        budget_currency=matter.budget_currency or "USD",
        budget_notification_threshold=matter.budget_notification_threshold,
        billing_cycle=matter.billing_cycle or "monthly",
        billing_method=matter.billing_method or "hourly",
        hourly_rate=matter.hourly_rate,
        contingency_percentage=matter.contingency_percentage,
        tax_rate=matter.tax_rate,
        assignments=assignments,
        budget_utilization=budget,
        memory_content=matter.memory_content,
        cloud_folder=matter.cloud_folder,
        smb_folders=matter.smb_folders,
        primary_plugin=matter.primary_plugin,
        plugin_workflow_state=matter.plugin_workflow_state,
        created_at=matter.created_at or updated_at,
        updated_at=updated_at,
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
            selectinload(Matter.partner_attorney),
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
        expense_rows = (
            await db.execute(
                select(
                    Expense.matter_id,
                    func.coalesce(func.sum(expense_client_amount_expression()), 0),
                )
                .where(
                    Expense.matter_id.in_(matter_ids),
                    Expense.tenant_id == tenant_id,
                    Expense.is_billable.is_(True),
                )
                .group_by(Expense.matter_id)
            )
        ).all()
        for matter_id, amount in expense_rows:
            billed_map[matter_id] = billed_map.get(matter_id, Decimal("0")) + Decimal(
                str(amount or 0)
            )

    items = []
    for m in matters:
        client_name = getattr(m.client, "display_name", None) if m.client else None
        attorney_name = (
            getattr(m.attorney_of_record, "full_name", None)
            if m.attorney_of_record
            else None
        )
        partner_attorney_name = (
            getattr(m.partner_attorney, "full_name", None)
            if m.partner_attorney
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
                slug=_matter_slug(m),
                matter_number=m.matter_number,
                matter_name=m.matter_name or "Untitled matter",
                description=m.description,
                matter_type=m.matter_type,
                practice_area=m.practice_area,
                status=m.status or "open",
                risk_level=m.risk_level,
                counterparty=m.counterparty,
                primary_plugin=m.primary_plugin,
                client_name=client_name,
                attorney_of_record_name=attorney_name,
                partner_attorney_name=partner_attorney_name,
                stage=m.stage,
                assigned_to=assigned_to,
                budget_amount=m.budget_amount,
                total_billed=total_billed,
                budget_utilization_pct=util_pct,
                is_overdue=(m.status or "open") in ("active",) and bool(next_deadline),
                next_deadline=next_deadline,
                cloud_folder=m.cloud_folder,
                created_at=m.created_at or datetime.now(timezone.utc),
                updated_at=m.updated_at,
            )
        )

    return MatterListResponse(items=items, total=total, page=page, page_size=page_size)


async def _matter_field_values(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    column,
    *,
    limit: int = 100,
) -> list[str]:
    """Return trimmed, case-insensitively unique tenant values for a field."""
    rows = (
        await db.execute(
            select(column)
            .where(
                Matter.tenant_id == tenant_id,
                column.is_not(None),
                func.length(func.trim(column)) > 0,
            )
            .distinct()
            .order_by(column)
            .limit(limit)
        )
    ).scalars()

    values: list[str] = []
    seen: set[str] = set()
    for raw_value in rows:
        value = raw_value.strip()
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


@router.get("/field-options", response_model=MatterFieldOptions)
async def get_matter_field_options(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MatterFieldOptions:
    """List firm-used values for matter fields that also accept new values."""
    user = await get_current_user(request, db)
    tenant_id = user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    return MatterFieldOptions(
        matter_types=await _matter_field_values(db, tenant_id, Matter.matter_type),
        roles=await _matter_field_values(db, tenant_id, Matter.role),
        jurisdictions=await _matter_field_values(db, tenant_id, Matter.jurisdiction),
        counterparties=await _matter_field_values(db, tenant_id, Matter.counterparty),
    )


@router.post("", status_code=201, response_model=MatterResponse)
async def create_matter(
    body: MatterCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MatterResponse:
    """Create a new matter with optional initial assignments."""
    user = await get_current_user(request, db)
    tenant_id = user.tenant_id
    await set_tenant_context(db, str(tenant_id))

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

    retention_until = (
        _parse_date(body.key_dates.get("retention_until")) if body.key_dates else None
    )
    if not retention_until:
        retention_until = date_type.today() + timedelta(days=365 * 7)

    matter = Matter(
        tenant_id=tenant_id,
        user_id=user.id,
        slug=slug,
        matter_name=body.matter_name,
        description=body.description,
        matter_type=_matter_type(body.matter_type),
        role=body.role,
        counterparty=body.counterparty,
        jurisdiction=body.jurisdiction,
        venue=body.venue,
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
    # Human-readable matter number, assigned once at creation.
    await assign_matter_number(db, matter)
    db.add(matter)
    await db.flush()

    # Cloud folder provisioning is fire-and-forget — it can take several seconds
    # and must not block the create-matter response.
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if tenant and tenant.cloud_root_folder:
        matter.cloud_folder = _cloud_folder_status("provisioning")

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
        try:
            await share_matter_folders(
                db=db,
                tenant_id=str(tenant_id),
                cloud_folder=matter.cloud_folder,
                user_emails=assigned_emails,
            )
        except Exception:
            logger.warning(
                "Failed to share cloud folders for matter %s",
                matter.id,
                exc_info=True,
            )

    matter_id = matter.id
    # The trigger and matter save share one transaction: neither can be lost.
    await enqueue_matter_event(
        db,
        matter=matter,
        trigger_event="matter_created",
        actor_user_id=user.id,
    )
    await db.commit()
    await set_tenant_context(db, str(tenant_id))
    await db.refresh(matter)
    if tenant and tenant.cloud_root_folder:
        asyncio.create_task(
            _provision_cloud_folders(
                str(matter.id),
                str(tenant_id),
                slug,
                tenant.cloud_root_folder,
                matter_name=body.matter_name,
                matter_number=matter.matter_number,
            )
        )

    # Reload with relationships
    result = await db.execute(
        select(Matter)
        .options(
            selectinload(Matter.assignments).selectinload(MatterAssignment.user),
            selectinload(Matter.client),
            selectinload(Matter.attorney_of_record),
        )
        .where(Matter.id == matter_id)
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
            selectinload(Matter.partner_attorney),
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

    my_matter_ids = [matter.id for matter in matters]
    my_billed_map: dict[uuid.UUID, Decimal] = {}
    if my_matter_ids:
        my_time_rows = (
            await db.execute(
                select(
                    TimeEntry.matter_id, func.coalesce(func.sum(TimeEntry.amount), 0)
                )
                .where(
                    TimeEntry.tenant_id == tenant_id,
                    TimeEntry.matter_id.in_(my_matter_ids),
                    TimeEntry.is_billable.is_(True),
                )
                .group_by(TimeEntry.matter_id)
            )
        ).all()
        my_billed_map = {row[0]: Decimal(str(row[1] or 0)) for row in my_time_rows}
        my_expense_rows = (
            await db.execute(
                select(
                    Expense.matter_id,
                    func.coalesce(func.sum(expense_client_amount_expression()), 0),
                )
                .where(
                    Expense.tenant_id == tenant_id,
                    Expense.matter_id.in_(my_matter_ids),
                    Expense.is_billable.is_(True),
                )
                .group_by(Expense.matter_id)
            )
        ).all()
        for matter_id, amount in my_expense_rows:
            my_billed_map[matter_id] = my_billed_map.get(
                matter_id, Decimal("0")
            ) + Decimal(str(amount or 0))

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
        partner_attorney_name = (
            getattr(m.partner_attorney, "full_name", None)
            if m.partner_attorney
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
                slug=_matter_slug(m),
                matter_number=m.matter_number,
                matter_name=m.matter_name or "Untitled matter",
                description=m.description,
                matter_type=m.matter_type,
                practice_area=m.practice_area,
                status=m.status or "open",
                risk_level=m.risk_level,
                counterparty=m.counterparty,
                primary_plugin=m.primary_plugin,
                client_name=client_name,
                attorney_of_record_name=attorney_name,
                partner_attorney_name=partner_attorney_name,
                stage=m.stage,
                assigned_to=assigned_to,
                budget_amount=m.budget_amount,
                total_billed=my_billed_map.get(m.id, Decimal("0")),
                budget_utilization_pct=(
                    round(
                        float(
                            my_billed_map.get(m.id, Decimal("0"))
                            / m.budget_amount
                            * 100
                        ),
                        1,
                    )
                    if m.budget_amount and m.budget_amount > 0
                    else None
                ),
                is_overdue=overdue_label is not None and "overdue" in overdue_label,
                next_deadline=next_deadline,
                cloud_folder=m.cloud_folder,
                created_at=m.created_at or datetime.now(timezone.utc),
                updated_at=m.updated_at,
                my_role=my_role or "associate",
                my_assignment_id=my_assignment_id,
                is_active_working=bool(is_active_working),
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
    billed_expense_q = await db.execute(
        select(func.coalesce(func.sum(expense_client_amount_expression()), 0)).where(
            Expense.tenant_id == tenant_id, Expense.is_billable.is_(True)
        )
    )
    total_billed += Decimal(str(billed_expense_q.scalar() or 0))

    unbilled_q = await db.execute(
        select(func.coalesce(func.sum(TimeEntry.amount), 0)).where(
            TimeEntry.tenant_id == tenant_id,
            TimeEntry.is_billable.is_(True),
            TimeEntry.invoice_id.is_(None),
        )
    )
    total_unbilled = Decimal(str(unbilled_q.scalar() or 0))
    unbilled_expense_q = await db.execute(
        select(func.coalesce(func.sum(expense_client_amount_expression()), 0)).where(
            Expense.tenant_id == tenant_id,
            Expense.is_billable.is_(True),
            Expense.invoice_id.is_(None),
        )
    )
    total_unbilled += Decimal(str(unbilled_expense_q.scalar() or 0))

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


async def _matter_detail_response(
    db: AsyncSession, matter: Matter, tenant_id: uuid.UUID
) -> MatterResponse:
    """Build the full detail response, including computed budget utilization."""
    budget = await _compute_budget_utilization(db, matter.id, tenant_id)
    budget.budget_amount = matter.budget_amount
    budget.budget_currency = matter.budget_currency or "USD"
    if budget.budget_amount and budget.budget_amount > 0:
        budget.utilization_pct = round(
            float(budget.total_billed / budget.budget_amount * 100), 1
        )
        budget.remaining = budget.budget_amount - budget.total_billed
    return _matter_to_response(matter, budget)


@router.get("/by-number/{matter_number}", response_model=MatterResponse)
async def get_matter_by_number(
    matter_number: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Resolve a human-readable matter number to full matter detail.

    This is what turns ``SMIT0001`` -- read off a letter, quoted on a call, or
    typed into the address bar -- back into a matter.  Resolution is
    tenant-scoped, so one firm's number never resolves inside another's
    workspace even when the numbers coincide.
    """
    user = await get_current_user(request, db)
    matter = await _get_matter_by_number_or_404(db, matter_number, user.tenant_id)
    return await _matter_detail_response(db, matter, user.tenant_id)


@router.get("/{matter_id}", response_model=MatterResponse)
async def get_matter(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get full matter detail with assignments, budget, and client info."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)
    return await _matter_detail_response(db, matter, user.tenant_id)


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
    prior_stage = matter.stage

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

    if matter.stage != prior_stage:
        await enqueue_matter_event(
            db,
            matter=matter,
            trigger_event="matter_stage_changed",
            actor_user_id=user.id,
        )
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    await db.refresh(matter)
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)

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
    acknowledge_warnings: bool = False,
    reason: str | None = None,
):
    """Soft-close a matter, refusing to strand the client's money.

    Unbilled work and a held trust balance block the close outright: both are
    money that belongs to somebody else, and a closed matter is where it goes
    to be forgotten. Warnings -- open tasks, live signatures, unfinished
    paperwork -- are shown first and pass with ``acknowledge_warnings``, so a
    deliberate close is one decision rather than an argument.
    """
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)
    if matter.is_closed:
        return None

    readiness = await close_readiness(db, user.tenant_id, matter)
    if not readiness["can_close"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "matter_close_blocked",
                "message": "Resolve the outstanding items before closing this matter.",
                "checks": [
                    c for c in readiness["checks"] if c["blocking"] and not c["clear"]
                ],
            },
        )
    if readiness["warning_count"] and not acknowledge_warnings:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "matter_close_needs_acknowledgement",
                "message": "Confirm you want to close with these items outstanding.",
                "checks": [
                    c
                    for c in readiness["checks"]
                    if not c["blocking"] and not c["clear"]
                ],
            },
        )

    matter.is_closed = True
    matter.status = "closed"
    db.add(
        MatterEvent(
            tenant_id=user.tenant_id,
            matter_id=matter.id,
            event_type="matter_closed",
            title="Matter closed",
            content=(
                reason.strip()[:2000] if reason and reason.strip() else "Matter closed."
            ),
            note_type="system",
            created_by=user.id,
        )
    )
    await db.commit()
    # Intake owns the packet's own follow-ups and portal invitation; its
    # reconcile pass cancels them for a closed matter rather than leaving a
    # client chasing paperwork on a case that is over.
    try:
        from app.services import matter_intake

        packet = await matter_intake.get_packet(
            db, user.tenant_id, matter.id, lock=True
        )
        if packet is not None:
            await matter_intake.reconcile(db, packet)
            await db.commit()
    except Exception:
        logger.exception(
            "Intake follow-ups could not be cancelled for closed matter %s", matter.id
        )
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)
    return None


@router.get("/{matter_id}/close-readiness")
async def matter_close_readiness(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """What this matter still owes before it can be closed."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)
    return await close_readiness(db, user.tenant_id, matter)


@router.post("/{matter_id}/reopen", status_code=204)
async def reopen_matter(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Reopen a closed matter. Closing is reversible; it is not deletion."""
    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)
    if not matter.is_closed:
        return None
    matter.is_closed = False
    matter.status = "active"
    db.add(
        MatterEvent(
            tenant_id=user.tenant_id,
            matter_id=matter.id,
            event_type="matter_reopened",
            title="Matter reopened",
            content="Matter reopened.",
            note_type="system",
            created_by=user.id,
        )
    )
    await db.commit()
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)
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
    await _invalidate_matter_context_cache(current_user.tenant_id, matter.id)

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
        select(MatterAssignment)
        .where(
            MatterAssignment.id == assignment_id,
            MatterAssignment.matter_id == matter.id,
            MatterAssignment.tenant_id == user.tenant_id,
        )
        .with_for_update(of=MatterAssignment)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    revoked_user_id = assignment.user_id
    sms_calendar_rows = (
        await db.execute(
            select(Task.id, Task.assigned_to_user_id, Task.created_by_user_id).where(
                Task.tenant_id == user.tenant_id,
                Task.matter_id == matter.id,
                task_is_sms_expression(tenant_id=user.tenant_id),
            )
        )
    ).all()
    cleanup_task_ids = [
        task_id
        for task_id, assigned_to_user_id, created_by_user_id in sms_calendar_rows
        if (assigned_to_user_id or created_by_user_id) == revoked_user_id
    ]
    try:
        for task_id in cleanup_task_ids:
            await remove_task_from_calendars_now(
                str(task_id), str(user.tenant_id), str(revoked_user_id)
            )
    except RuntimeError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail="External calendar cleanup is unavailable; assignment was not removed",
        ) from exc

    await db.delete(assignment)
    await db.commit()
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)
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

    # Serialize note requests on this authorized matter. The event persists the
    # receipt even if the note is later deleted, preventing retry resurrection.
    note_id = uuid.uuid4()
    event_id = uuid.uuid4()
    digest = None
    if body.request_id:
        await db.execute(
            select(Matter.id)
            .where(
                Matter.id == matter.id,
                Matter.tenant_id == user.tenant_id,
            )
            .with_for_update()
        )
        identity = (
            f"lawhand:note:{user.tenant_id}:{user.id}:{matter.id}:{body.request_id}"
        )
        note_id = uuid.uuid5(uuid.NAMESPACE_URL, identity)
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, identity + ":event")
        payload = body.model_dump(mode="json", exclude={"request_id", "hours"})
        payload["hours"] = (
            str(body.hours.normalize()) if body.hours is not None else None
        )
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        receipt = (
            await db.execute(
                select(MatterEvent).where(
                    MatterEvent.id == event_id,
                    MatterEvent.tenant_id == user.tenant_id,
                    MatterEvent.matter_id == matter.id,
                    MatterEvent.created_by == user.id,
                )
            )
        ).scalar_one_or_none()
        if receipt:
            if (receipt.metadata_json or {}).get("note_request_digest") != digest:
                raise HTTPException(
                    409, "This note request was already used for different content."
                )
            note = (
                await db.execute(
                    select(MatterNote).where(
                        MatterNote.id == note_id,
                        MatterNote.tenant_id == user.tenant_id,
                        MatterNote.matter_id == matter.id,
                        MatterNote.author_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            if note is None:
                raise HTTPException(
                    409, "This note was saved and later deleted. Start a new note."
                )
            return _note_response(note, user.full_name)

    note = MatterNote(
        id=note_id,
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
        id=event_id,
        metadata_json={"note_request_digest": digest} if digest else None,
        tenant_id=user.tenant_id,
        matter_id=matter.id,
        event_type="note",
        title=f"Note: {body.title}"[:500],
        content=body.content,
        note_type=body.note_type,
        created_by=user.id,
    )
    db.add(event)

    # Snapshot identities before commit; transaction-local RLS must be rebound.
    tenant_id, saved_matter_id, author_name = user.tenant_id, matter.id, user.full_name
    await db.commit()
    await set_tenant_context(db, str(tenant_id))
    await db.refresh(note)
    await _invalidate_matter_context_cache(tenant_id, saved_matter_id)
    return _note_response(note, author_name)


def _note_response(note: MatterNote, author_name: str) -> MatterNoteResponse:
    return MatterNoteResponse(
        id=str(note.id),
        matter_id=str(note.matter_id),
        author_id=str(note.author_id) if note.author_id else None,
        author_name=author_name,
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
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)

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
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)
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
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)

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
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)

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
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)

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

    # Resolve timekeepers in one query so the matter table can name who worked.
    user_ids = {e.user_id for e in entries if e.user_id}
    names: dict[uuid.UUID, str] = {}
    if user_ids:
        name_rows = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(user_ids))
        )
        names = {uid: full_name for uid, full_name in name_rows.all()}

    return [
        {
            "id": str(e.id),
            "matter_id": str(e.matter_id),
            "user_id": str(e.user_id),
            "user_name": names.get(e.user_id),
            "description": e.description,
            "hours": str(e.hours),
            "hourly_rate": str(e.hourly_rate),
            "amount": str(e.amount),
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
            # Money stays Decimal-as-string, matching every other billing route.
            "subtotal": str(i.subtotal),
            "tax_amount": str(i.tax_amount),
            "total": str(i.total),
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
                    "quantity": str(li.quantity),
                    "unit_price": str(li.unit_price),
                    "amount": str(li.amount),
                }
                for li in i.line_items
            ],
            "payments": [
                {
                    "id": str(p.id),
                    "amount": str(p.amount),
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
    await _invalidate_matter_context_cache(user.tenant_id, matter.id)
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


@router.get("/{matter_id}/email-attachments/{document_id}/preview")
async def preview_email_attachment(
    matter_id: str,
    document_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    import base64
    from app.services.matter_mail_attachments import reviewed_attachment

    user = await get_current_user(request, db)
    matter = await _get_matter_or_404(db, matter_id, user.tenant_id)
    attachment, digest = await reviewed_attachment(
        db, user.tenant_id, matter.id, document_id
    )
    from fastapi.responses import JSONResponse

    return JSONResponse(
        {
            "document_id": document_id,
            "filename": attachment.filename,
            "content_type": attachment.content_type,
            "sha256": digest,
            "content_base64": base64.b64encode(attachment.content).decode("ascii"),
        },
        headers={"Cache-Control": "no-store"},
    )


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
      <p>{escape(email_body).replace(chr(10), "<br>")}</p>
      <hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">
      <p style="font-size:12px;color:#999;">
        Re: {escape(matter.matter_name)}
        {(" — " + escape(matter.case_number)) if matter.case_number else ""}
      </p>
    </div>
    """

    # Token refresh may commit and expire ORM objects. Snapshot the authorized
    # identifiers before selecting the actor's or firm's connected mailbox.
    tenant_id, actor_id = user.tenant_id, user.id
    # Sending can refresh a provider token, which commits and expires every
    # attribute on this row. Everything needed afterwards is read now.
    authorized_matter_id, contact_id = matter.id, matter.client_contact_id
    matter_slug, matter_cloud_folder = matter.slug, matter.cloud_folder
    from app.services.matter_mail_attachments import collect_reviewed_attachments

    attachments = await collect_reviewed_attachments(
        db, tenant_id, authorized_matter_id, body.get("attachments", [])
    )
    delivery = await send_client_email(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        to=[to_email],
        subject=subject,
        html_body=html_body,
        text_body=email_body,
        smtp_service=EmailService(),
        **({"attachments": attachments} if attachments else {}),
    )
    sent = delivery.result == EmailDeliveryResult.SENT
    uncertain = delivery.delivery_certainty == DELIVERY_OUTCOME_UNKNOWN
    await set_tenant_context(db, str(tenant_id))

    # Log regardless of send outcome (outbound attempt is recorded)
    log = CommunicationLog(
        tenant_id=tenant_id,
        direction="outbound",
        channel="email",
        status="sent" if sent else "delivery_unknown" if uncertain else "failed",
        subject=subject,
        body=email_body,
        matter_id=authorized_matter_id,
        contact_id=contact_id,
        created_by_user_id=actor_id,
        participants={"to": [to_email], "provider": delivery.provider},
    )
    db.add(log)
    await db.commit()
    await set_tenant_context(db, str(tenant_id))
    await db.refresh(log)
    # Keep the firm's own copy of what it sent. Only a message that actually
    # left gets filed: a failed attempt is a log row, not correspondence.
    if sent:
        from app.services.correspondence_capture import file_outbound_email

        await file_outbound_email(
            db,
            tenant_id=tenant_id,
            matter_id=authorized_matter_id,
            matter_slug=matter_slug,
            cloud_folder=matter_cloud_folder,
            actor_user_id=actor_id,
            to=[to_email],
            subject=subject,
            text_body=email_body,
            attachments=attachments,
            communication=log,
        )
    await _invalidate_matter_context_cache(tenant_id, authorized_matter_id)

    if not sent:
        if uncertain:
            raise HTTPException(
                status_code=409,
                detail="Delivery is unconfirmed. Check the sending mailbox's Sent Items "
                "before sending again; the provider may have accepted this message. "
                "The attempt was recorded on the matter.",
            )
        status_code, detail = email_delivery_http_error(
            delivery.result, action="Client email"
        )
        if (
            delivery.provider
            or delivery.result == EmailDeliveryResult.REAUTHORIZATION_REQUIRED
        ):
            detail = delivery.detail
        raise HTTPException(
            status_code=status_code,
            detail=f"{detail} The failed outbound attempt was recorded on the matter.",
        )

    return {
        "id": str(log.id),
        "sent": bool(sent),
        "to": to_email,
        "subject": subject,
        "matter_id": str(authorized_matter_id),
        "provider": delivery.provider,
        "logged_at": log.occurred_at.isoformat(),
    }


# ── Cloud Files ──────────────────────────────────────────────────────────────


@router.get("/{matter_id}/cloud-files")
async def get_matter_cloud_files(
    matter_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search cloud integrations for files related to this matter."""
    await set_tenant_context(db, str(current_user.tenant_id))
    matter = await _get_matter_or_404(db, matter_id, current_user.tenant_id)
    return await _build_matter_cloud_files_response(
        db, current_user.tenant_id, current_user.id, matter
    )


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
            status=matter.cloud_folder.get("_status", "provisioned"),
            providers=matter.cloud_folder,
        )
    return MatterCloudFolderStatus(status="not_provisioned", providers={})


def _apply_cloud_provider_metadata(
    matter: Matter, provider: str, provider_metadata: dict
) -> dict:
    """Merge provider folder metadata into Matter.cloud_folder."""
    cloud_folder = dict(matter.cloud_folder or {})
    metadata = dict(provider_metadata)
    metadata["path"] = (
        metadata.get("path")
        or f"{ROOT_FOLDER_NAME}/{metadata.get('folder_name') or matter.slug}"
    )
    cloud_folder[provider] = metadata
    cloud_folder["path"] = (
        cloud_folder.get("path")
        or f"{ROOT_FOLDER_NAME}/{canonical_matter_folder_name(matter.matter_name, matter.id, matter.slug, getattr(matter, 'matter_number', None))}"
    )
    cloud_folder["subfolder_paths"] = {
        **(cloud_folder.get("subfolder_paths") or {}),
        **{
            sub: f"{cloud_folder['path']}/{sub}"
            for sub in metadata.get("subfolders", {})
        },
    }
    cloud_folder.update(_cloud_folder_status("provisioned"))
    matter.cloud_folder = cloud_folder
    return cloud_folder


def _cloud_provider_folder_id(
    metadata: dict | None, *, allow_id: bool = True
) -> str | None:
    if not isinstance(metadata, dict):
        return None
    folder_id = metadata.get("matter_folder_id")
    if not folder_id and allow_id:
        folder_id = metadata.get("id")
    return str(folder_id) if folder_id else None


def _cloud_context_folders(cloud_folder: dict | None) -> list[dict]:
    raw = (cloud_folder or {}).get("context_folders") or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _build_cloud_context_folder(
    provider: str, provider_metadata: dict, label: str | None
) -> dict:
    clean_label = (label or "").strip() or None
    return {
        "id": str(uuid.uuid4()),
        "provider": provider,
        "label": clean_label,
        "matter_folder_id": provider_metadata.get("matter_folder_id"),
        "folder_name": provider_metadata.get("folder_name") or "",
        "url": provider_metadata.get("url") or "",
        "subfolders": provider_metadata.get("subfolders") or {},
        "drive_id": provider_metadata.get("drive_id"),
        "added_at": datetime.now(timezone.utc).isoformat(),
    }


def _assert_cloud_context_not_duplicate(
    matter: Matter, provider: str, folder_id: str | None
) -> None:
    if not folder_id:
        return
    cloud_folder = matter.cloud_folder or {}
    primary_id = _cloud_provider_folder_id(cloud_folder.get(provider), allow_id=True)
    if primary_id == folder_id:
        raise HTTPException(
            status_code=409,
            detail="That folder is already mapped as the primary matter folder",
        )
    for folder in _cloud_context_folders(cloud_folder):
        if folder.get("provider") != provider:
            continue
        existing_id = _cloud_provider_folder_id(folder, allow_id=False)
        if existing_id == folder_id:
            raise HTTPException(
                status_code=409,
                detail="That folder is already linked as matter context",
            )


async def _repair_tenant_cloud_root(
    db: AsyncSession, tenant: Tenant, tenant_id: uuid.UUID
) -> dict:
    """Refresh tenant root metadata so matter reconnects use the discovered root."""
    existing_root = tenant.cloud_root_folder
    repair_needed = cloud_root_binding_repair_needed(existing_root)
    if repair_needed:
        logger.warning(
            "Tenant cloud root binding requires administrator repair for %s: %s",
            tenant_id,
            ", ".join(repair_needed),
        )
        return existing_root if isinstance(existing_root, dict) else {}

    cloud_root = existing_root or {}
    try:
        fresh = await initialize_cloud_root_folder(
            db, str(tenant_id), existing_root=cloud_root
        )
        if fresh:
            cloud_root = {**cloud_root, **fresh}
            tenant.cloud_root_folder = cloud_root
    except Exception as exc:
        logger.warning("Tenant cloud root repair failed for %s: %s", tenant_id, exc)
    return cloud_root


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
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cloud_root = await _repair_tenant_cloud_root(db, tenant, tenant_id)
    if not cloud_root:
        raise HTTPException(
            status_code=422,
            detail="No cloud credentials configured for this tenant",
        )

    try:
        cloud_folder = await initialize_matter_folders(
            db=db,
            tenant_id=str(tenant_id),
            matter_slug=matter.slug,
            cloud_root=cloud_root,
            matter_id=matter.id,
            folder_name=matter.matter_name,
            existing_folder=matter.cloud_folder,
            matter_number=matter.matter_number,
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

    matter.cloud_folder = {**(matter.cloud_folder or {}), **cloud_folder}
    await _share_matter_with_assignees(db, tenant_id, matter)

    await db.commit()
    await db.refresh(matter)
    await _invalidate_matter_context_cache(tenant_id, matter.id)

    return MatterCloudFolderStatus(
        status="provisioned",
        providers=matter.cloud_folder,
    )


@router.patch(
    "/{matter_id}/cloud-folder/{provider}/remap",
    response_model=MatterCloudFolderStatus,
)
async def remap_matter_cloud_folder(
    matter_id: str,
    provider: str,
    body: MatterCloudFolderRemapRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Remap one provider for this matter to an existing cloud folder."""
    if provider not in SUPPORTED_CLOUD_FOLDER_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported cloud provider")
    if not (body.folder_id or body.folder_url or body.folder_name):
        raise HTTPException(
            status_code=400,
            detail="Provide a folder ID, folder URL, or folder name",
        )

    current_user = await require_admin(request, db)
    tenant_id = current_user.tenant_id
    tenant_id_str = str(tenant_id)
    await set_tenant_context(db, tenant_id_str)

    matter = await _get_matter_or_404(db, matter_id, tenant_id)
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cloud_root = await _repair_tenant_cloud_root(db, tenant, tenant_id)
    if not cloud_root:
        raise HTTPException(
            status_code=422,
            detail="No cloud root folder configured for this tenant",
        )

    try:
        provider_metadata = await resolve_cloud_folder_reference(
            db=db,
            tenant_id=tenant_id_str,
            provider=provider,
            cloud_root=cloud_root,
            folder_id=body.folder_id,
            folder_url=body.folder_url,
            folder_name=body.folder_name,
            create_if_missing=body.create_if_missing,
            ensure_subfolders=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning("Cloud folder remap failed for matter %s: %s", matter_id, exc)
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        await ensure_matter_marker(
            db,
            tenant_id_str,
            matter.id,
            provider,
            provider_metadata,
            canonical_matter_folder_name(
                matter.matter_name,
                matter.id,
                matter.slug,
                getattr(matter, "matter_number", None),
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail="Could not verify the folder identity marker; reconnect and retry",
        ) from exc

    provider_metadata = await build_matter_folder_metadata(
        db, tenant_id_str, provider, provider_metadata["matter_folder_id"]
    )
    providers = _apply_cloud_provider_metadata(matter, provider, provider_metadata)
    await _share_matter_with_assignees(db, tenant_id, matter)
    await db.commit()
    await _invalidate_matter_context_cache(tenant_id, matter.id)

    return MatterCloudFolderStatus(status="provisioned", providers=providers)


@router.post(
    "/{matter_id}/cloud-folder/context",
    response_model=MatterCloudFolderStatus,
)
async def add_matter_cloud_context_folder(
    matter_id: str,
    body: MatterCloudContextFolderRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Attach an additional provider folder as read/search context for a matter."""
    provider = (body.provider or "").strip().lower()
    if provider not in SUPPORTED_CLOUD_FOLDER_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported cloud provider")
    if not (body.folder_id or body.folder_url or body.folder_name):
        raise HTTPException(
            status_code=400,
            detail="Provide a folder ID, folder URL, or folder name",
        )

    current_user = await get_current_user(request, db)
    tenant_id = current_user.tenant_id
    tenant_id_str = str(tenant_id)
    await set_tenant_context(db, tenant_id_str)

    matter = await _get_matter_or_404(db, matter_id, tenant_id)
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cloud_root = await _repair_tenant_cloud_root(db, tenant, tenant_id)
    if body.folder_name and not (body.folder_id or body.folder_url) and not cloud_root:
        raise HTTPException(
            status_code=422,
            detail="No cloud root folder configured for folder-name lookup",
        )

    try:
        provider_metadata = await resolve_cloud_folder_reference(
            db=db,
            tenant_id=tenant_id_str,
            provider=provider,
            cloud_root=cloud_root or {},
            folder_id=body.folder_id,
            folder_url=body.folder_url,
            folder_name=body.folder_name,
            create_if_missing=body.create_if_missing,
            ensure_subfolders=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning(
            "Cloud context folder add failed for matter %s: %s", matter_id, exc
        )
        raise HTTPException(status_code=422, detail=str(exc))

    provider_folder_id = _cloud_provider_folder_id(provider_metadata, allow_id=False)
    _assert_cloud_context_not_duplicate(matter, provider, provider_folder_id)

    cloud_folder = dict(matter.cloud_folder or {})
    context_folders = _cloud_context_folders(cloud_folder)
    context_folders.append(
        _build_cloud_context_folder(provider, provider_metadata, body.label)
    )
    cloud_folder["context_folders"] = context_folders
    matter.cloud_folder = cloud_folder

    await _share_matter_with_assignees(db, tenant_id, matter)
    await db.commit()
    await _invalidate_matter_context_cache(tenant_id, matter.id)

    return MatterCloudFolderStatus(status="provisioned", providers=cloud_folder)


@router.delete(
    "/{matter_id}/cloud-folder/context/{context_folder_id}",
    response_model=MatterCloudFolderStatus,
)
async def remove_matter_cloud_context_folder(
    matter_id: str,
    context_folder_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Remove an additional context folder mapping from a matter."""
    current_user = await get_current_user(request, db)
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    matter = await _get_matter_or_404(db, matter_id, tenant_id)
    cloud_folder = dict(matter.cloud_folder or {})
    context_folders = _cloud_context_folders(cloud_folder)
    next_context = [
        folder for folder in context_folders if folder.get("id") != context_folder_id
    ]
    if len(next_context) == len(context_folders):
        raise HTTPException(status_code=404, detail="Context folder mapping not found")

    if next_context:
        cloud_folder["context_folders"] = next_context
    else:
        cloud_folder.pop("context_folders", None)
    matter.cloud_folder = cloud_folder or None

    await db.commit()
    await _invalidate_matter_context_cache(tenant_id, matter.id)

    status = "provisioned" if matter.cloud_folder else "not_provisioned"
    return MatterCloudFolderStatus(status=status, providers=matter.cloud_folder or {})


@router.patch(
    "/{matter_id}/cloud-folder/{provider}/rename",
    response_model=MatterCloudFolderStatus,
)
async def rename_matter_cloud_folder(
    matter_id: str,
    provider: str,
    body: MatterCloudFolderRenameRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Rename one provider folder for this matter and update stored metadata."""
    if provider not in SUPPORTED_CLOUD_FOLDER_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unsupported cloud provider")

    current_user = await get_current_user(request, db)
    tenant_id = current_user.tenant_id
    tenant_id_str = str(tenant_id)
    await set_tenant_context(db, tenant_id_str)

    matter = await _get_matter_or_404(db, matter_id, tenant_id)
    provider_data = (matter.cloud_folder or {}).get(provider) or {}
    folder_id = provider_data.get("matter_folder_id") or provider_data.get("id")
    if not folder_id:
        raise HTTPException(
            status_code=422,
            detail="This matter is not mapped to that cloud provider",
        )

    try:
        provider_metadata = await rename_cloud_folder(
            db=db,
            tenant_id=tenant_id_str,
            provider=provider,
            folder_id=folder_id,
            new_name=body.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning("Cloud folder rename failed for matter %s: %s", matter_id, exc)
        raise HTTPException(status_code=422, detail=str(exc))

    providers = _apply_cloud_provider_metadata(matter, provider, provider_metadata)
    await db.commit()
    await _invalidate_matter_context_cache(tenant_id, matter.id)

    return MatterCloudFolderStatus(status="provisioned", providers=providers)


@router.post("/{matter_id}/cloud-folder/sync")
async def sync_matter_cloud_folder(
    matter_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Provision if needed, refresh cloud metadata, and return matter cloud files."""
    current_user = await get_current_user(request, db)
    tenant_id = current_user.tenant_id
    tenant_id_str = str(tenant_id)
    await set_tenant_context(db, tenant_id_str)

    matter = await _get_matter_or_404(db, matter_id, tenant_id)
    provisioned = False

    if not matter.cloud_folder:
        tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = tenant_result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        cloud_root = await _repair_tenant_cloud_root(db, tenant, tenant_id)
        if not cloud_root:
            raise HTTPException(
                status_code=422,
                detail="No cloud credentials configured for this tenant",
            )

        cloud_folder = await initialize_matter_folders(
            db=db,
            tenant_id=tenant_id_str,
            matter_slug=matter.slug,
            cloud_root=cloud_root,
            matter_id=matter.id,
            folder_name=matter.matter_name,
            existing_folder=matter.cloud_folder,
            matter_number=matter.matter_number,
        )
        if not cloud_folder:
            raise HTTPException(
                status_code=422,
                detail="Cloud folder provisioning returned empty result",
            )
        matter.cloud_folder = {**(matter.cloud_folder or {}), **cloud_folder}
        provisioned = True

    await _share_matter_with_assignees(db, tenant_id, matter)
    await db.commit()
    await db.refresh(matter)
    await _invalidate_matter_context_cache(tenant_id, matter.id)

    sync_counts = await _cloud_sync.sync_matter_folders(
        db,
        tenant_id_str,
        matter.cloud_folder,
        user_id=str(current_user.id),
        matter_id=str(matter.id),
    )
    files = await _build_matter_cloud_files_response(
        db, tenant_id, current_user.id, matter
    )

    return {
        "status": "synced",
        "provisioned": provisioned,
        "providers": matter.cloud_folder or {},
        "sync_counts": sync_counts,
        "connected": files.get("connected", False),
        "files": files.get("files", []),
    }


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
