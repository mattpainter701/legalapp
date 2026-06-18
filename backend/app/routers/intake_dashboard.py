"""Receptionist-focused intake dashboard endpoints."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user, require_admin
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.intake_dashboard import LegacyCallRecord, PartnerRotationState
from app.models.plugin import Matter
from app.models.task import Task
from app.models.user import User
from app.schemas.intake_dashboard import (
    AssignNextResponse,
    AssignmentAvailabilityResponse,
    IntakeDashboardCallCreate,
    IntakeDashboardCallResponse,
    IntakeDashboardSearchResponse,
    IntakeSearchResult,
    RecentIntakeCaller,
    RecentIntakeCallersResponse,
    RotationRuleListResponse,
    RotationRuleResponse,
    RotationRuleUpsertRequest,
)
from app.services.intake_archive_import import normalize_phone
from app.services.task_notifications import notify_task_created

router = APIRouter(prefix="/api/intake/dashboard", tags=["intake-dashboard"])

INTAKE_CALL_EXPORT_FIELDS = [
    "call_date",
    "caller_name",
    "phone",
    "normalized_phone",
    "practice_area",
    "purpose",
    "notes",
    "outcome",
    "lead_status",
    "tabs3_partner_name",
    "tabs3_partner_user_id",
    "assigned_to_name",
    "assigned_to_user_id",
    "task_status",
    "task_completed_at",
    "logged_by_name",
    "logged_by_user_id",
    "contact_id",
    "lead_id",
    "communication_id",
]


def _practice_key(value: str | None) -> str:
    return (value or "general").strip().lower() or "general"


def _phone_digits_expr(column):
    return func.regexp_replace(func.coalesce(column, ""), "[^0-9]", "", "g")


def _contact_title(contact: Contact) -> str:
    return contact.display_name


def _contact_name_matches(contact: Contact, query: str) -> bool:
    if not query:
        return False
    haystack = " ".join(
        value
        for value in [
            contact.first_name,
            contact.last_name,
            contact.organization_name,
            contact.email,
            contact.display_name,
        ]
        if value
    ).lower()
    return query.lower() in haystack


def _phone_matches(*values: str | None, normalized_phone: str | None) -> bool:
    if not normalized_phone:
        return False
    candidates = {normalized_phone}
    if len(normalized_phone) == 10:
        candidates.add(f"1{normalized_phone}")
    return any(normalize_phone(value) in candidates for value in values if value)


def _match_metadata(*, name_match: bool, phone_match: bool, extra: dict | None = None) -> dict:
    matched_on = []
    if name_match:
        matched_on.append("name")
    if phone_match:
        matched_on.append("phone")
    metadata = {"matched_on": matched_on}
    if phone_match and not name_match:
        metadata["phone_only_match"] = True
        metadata["identity_confidence"] = "low"
    elif phone_match and name_match:
        metadata["identity_confidence"] = "high"
    elif name_match:
        metadata["identity_confidence"] = "medium"
    if extra:
        metadata.update(extra)
    return metadata


def _identity_score(
    *,
    name_match: bool,
    phone_match: bool,
    name_score: int,
    phone_only_score: int,
    combined_score: int,
) -> int:
    if name_match and phone_match:
        return combined_score
    if name_match:
        return name_score
    if phone_match:
        return phone_only_score
    return 0


async def _user_name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    row = result.scalar_one_or_none()
    return _user_name_from_row(row)


def _user_name_from_row(row: User | None) -> str | None:
    return row.full_name or row.email if row else None


def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=INTAKE_CALL_EXPORT_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _iso_datetime(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo:
        return value.astimezone(timezone.utc).isoformat()
    return value.replace(tzinfo=timezone.utc).isoformat()


async def _resolve_user_by_name(
    db: AsyncSession, tenant_id: uuid.UUID, attorney_name: str | None
) -> User | None:
    name = (attorney_name or "").strip()
    if not name:
        return None
    lowered = name.lower()
    return (
        await db.execute(
            select(User)
            .where(User.tenant_id == tenant_id, User.is_active.is_(True))
            .where(
                or_(
                    func.lower(User.full_name) == lowered,
                    func.lower(User.email) == lowered,
                    func.lower(User.full_name).like(f"%{lowered}%"),
                )
            )
            .order_by(User.full_name.asc().nullslast(), User.email.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _upsert_lead_assignment_task(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lead: Lead,
    assigned_to_user_id: uuid.UUID | None,
    created_by_user_id: uuid.UUID | None,
) -> Task | None:
    if not assigned_to_user_id:
        return None

    contact = (
        await db.execute(
            select(Contact).where(Contact.id == lead.contact_id, Contact.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    caller = contact.display_name if contact else "New intake lead"
    phone = contact.phone if contact else None
    external_ref = f"intake-dashboard:lead:{lead.id}:follow-up"
    description_bits = [
        "Urgent intake follow-up generated by the local intake dashboard.",
        f"Caller: {caller}",
        f"Phone: {phone}" if phone else "",
        f"Practice area: {lead.practice_area}" if lead.practice_area else "",
        f"Lead description: {lead.description}" if lead.description else "",
    ]

    task = (
        await db.execute(
            select(Task).where(
                Task.tenant_id == tenant_id,
                Task.external_ref == external_ref,
            )
        )
    ).scalar_one_or_none()
    if not task:
        task = Task(
            tenant_id=tenant_id,
            title=f"Urgent intake follow-up: {caller}",
            description="\n".join(bit for bit in description_bits if bit),
            task_type="follow_up",
            status="pending",
            priority="urgent",
            due_date=date.today(),
            contact_id=lead.contact_id,
            assigned_to_user_id=assigned_to_user_id,
            created_by_user_id=created_by_user_id,
            source="intake_dashboard",
            external_ref=external_ref,
        )
        db.add(task)
        await db.flush()
    else:
        task.title = f"Urgent intake follow-up: {caller}"
        task.description = "\n".join(bit for bit in description_bits if bit)
        task.priority = "urgent"
        task.status = "pending" if task.status == "cancelled" else task.status
        task.due_date = task.due_date or date.today()
        task.contact_id = lead.contact_id
        task.assigned_to_user_id = assigned_to_user_id

    return task


def _result_sort_key(item: IntakeSearchResult) -> tuple[int, int]:
    type_rank = {"matter": 0, "lead": 1, "contact": 2, "legacy_call": 3}
    return (-item.score, type_rank.get(item.result_type, 9))


def _log_participant(log: CommunicationLog, key: str) -> str | None:
    participants = log.participants or {}
    value = participants.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _log_field_from_body(log: CommunicationLog, prefix: str) -> str | None:
    body = log.body or ""
    marker = f"{prefix}:"
    for line in body.splitlines():
        if line.startswith(marker):
            value = line[len(marker) :].strip()
            return value or None
    return None


def _log_caller_name(log: CommunicationLog, contact: Contact | None) -> str:
    return (
        _log_participant(log, "caller_name")
        or (contact.display_name if contact else None)
        or log.subject.removeprefix("Inbound call:").strip()
        or "Unknown caller"
    )


async def _recent_lead_for_log(
    db: AsyncSession, tenant_id: uuid.UUID, log: CommunicationLog
) -> Lead | None:
    if not log.contact_id:
        return None
    return (
        await db.execute(
            select(Lead)
            .where(
                Lead.tenant_id == tenant_id,
                Lead.contact_id == log.contact_id,
            )
            .order_by(Lead.updated_at.desc(), Lead.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _assignment_task_for_lead(
    db: AsyncSession, tenant_id: uuid.UUID, lead: Lead | None
) -> Task | None:
    if not lead:
        return None
    return (
        await db.execute(
            select(Task)
            .where(
                Task.tenant_id == tenant_id,
                Task.external_ref == f"intake-dashboard:lead:{lead.id}:follow-up",
            )
            .order_by(Task.updated_at.desc(), Task.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _rotation_rule_with_active_users(
    db: AsyncSession, tenant_id: uuid.UUID, practice_area: str
) -> tuple[PartnerRotationState | None, list[uuid.UUID], dict[uuid.UUID, User]]:
    practice_key = _practice_key(practice_area)
    rule = (
        await db.execute(
            select(PartnerRotationState).where(
                PartnerRotationState.tenant_id == tenant_id,
                PartnerRotationState.practice_area == practice_key,
                PartnerRotationState.is_enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not rule and practice_key != "general":
        rule = (
            await db.execute(
                select(PartnerRotationState).where(
                    PartnerRotationState.tenant_id == tenant_id,
                    PartnerRotationState.practice_area == "general",
                    PartnerRotationState.is_enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
    if not rule:
        return None, [], {}

    eligible_ids = [uuid.UUID(str(value)) for value in (rule.eligible_user_ids or [])]
    if not eligible_ids:
        return rule, [], {}

    active_users = (
        await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.id.in_(eligible_ids),
                User.is_active.is_(True),
            )
        )
    ).scalars().all()
    active_by_id = {user.id: user for user in active_users}
    ordered = [uid for uid in eligible_ids if uid in active_by_id]
    return rule, ordered, active_by_id


@router.get("/assignment-availability", response_model=AssignmentAvailabilityResponse)
async def assignment_availability(
    practice_area: str = "general",
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    practice_key = _practice_key(practice_area)
    rule, ordered, _active_by_id = await _rotation_rule_with_active_users(
        db, tenant_id, practice_key
    )
    if not rule:
        return AssignmentAvailabilityResponse(
            practice_area=practice_key,
            can_assign=False,
            reason="No practice-specific or firm-wide rotation rule",
        )
    if not rule.eligible_user_ids:
        return AssignmentAvailabilityResponse(
            practice_area=practice_key,
            can_assign=False,
            reason="Rotation rule has no eligible users",
            rule_practice_area=rule.practice_area,
        )
    if not ordered:
        return AssignmentAvailabilityResponse(
            practice_area=practice_key,
            can_assign=False,
            reason="Rotation rule has no active eligible users",
            rule_practice_area=rule.practice_area,
        )
    return AssignmentAvailabilityResponse(
        practice_area=practice_key,
        can_assign=True,
        rule_practice_area=rule.practice_area,
        eligible_count=len(ordered),
    )


@router.get("/recent-callers", response_model=RecentIntakeCallersResponse)
async def recent_callers(
    limit: int = 20,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    if limit not in {10, 20, 50}:
        raise HTTPException(status_code=422, detail="Limit must be 10, 20, or 50")

    rows = (
        await db.execute(
            select(CommunicationLog, Contact, User)
            .outerjoin(
                Contact,
                (Contact.id == CommunicationLog.contact_id)
                & (Contact.tenant_id == tenant_id),
            )
            .outerjoin(
                User,
                (User.id == CommunicationLog.created_by_user_id)
                & (User.tenant_id == tenant_id),
            )
            .where(
                CommunicationLog.tenant_id == tenant_id,
                CommunicationLog.channel == "call",
                CommunicationLog.direction == "inbound",
            )
            .order_by(CommunicationLog.occurred_at.desc())
            .limit(limit)
        )
    ).all()

    callers = []
    for log, contact, creator in rows:
        lead = await _recent_lead_for_log(db, tenant_id, log)
        task = await _assignment_task_for_lead(db, tenant_id, lead)
        assigned_to_user_id = None
        if task and task.assigned_to_user_id:
            assigned_to_user_id = task.assigned_to_user_id
        elif lead and lead.assigned_to_user_id:
            assigned_to_user_id = lead.assigned_to_user_id
        callers.append(
            RecentIntakeCaller(
                id=log.id,
                caller_name=_log_caller_name(log, contact),
                phone=_log_participant(log, "phone") or (contact.phone if contact else None),
                normalized_phone=_log_participant(log, "normalized_phone"),
                practice_area=_log_field_from_body(log, "Practice area"),
                purpose=log.summary,
                notes=_log_field_from_body(log, "Notes"),
                contact_id=log.contact_id,
                lead_id=lead.id if lead else None,
                lead_status=lead.status if lead else None,
                assigned_to_user_id=assigned_to_user_id,
                assigned_to_name=await _user_name(db, assigned_to_user_id),
                task_id=task.id if task else None,
                task_status=task.status if task else None,
                task_priority=task.priority if task else None,
                task_due_date=task.due_date if task else None,
                task_completed_at=task.completed_at if task else None,
                created_by_user_id=log.created_by_user_id,
                created_by_name=_user_name_from_row(creator),
                occurred_at=log.occurred_at,
            )
        )
    return RecentIntakeCallersResponse(limit=limit, callers=callers)


@router.get("/calls/export")
async def export_call_records(
    start: date | None = Query(None, description="Inclusive start date"),
    end: date | None = Query(None, description="Inclusive end date"),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be before or equal to end")

    filters = [
        CommunicationLog.tenant_id == tenant_id,
        CommunicationLog.channel == "call",
        CommunicationLog.direction == "inbound",
    ]
    if start:
        filters.append(
            CommunicationLog.occurred_at >= datetime.combine(start, time.min, tzinfo=timezone.utc)
        )
    if end:
        filters.append(
            CommunicationLog.occurred_at <= datetime.combine(end, time.max, tzinfo=timezone.utc)
        )

    log_rows = (
        await db.execute(
            select(CommunicationLog, Contact, User)
            .outerjoin(
                Contact,
                (Contact.id == CommunicationLog.contact_id)
                & (Contact.tenant_id == tenant_id),
            )
            .outerjoin(
                User,
                (User.id == CommunicationLog.created_by_user_id)
                & (User.tenant_id == tenant_id),
            )
            .where(*filters)
            .order_by(CommunicationLog.occurred_at.desc())
        )
    ).all()

    contact_ids = [log.contact_id for log, _contact, _creator in log_rows if log.contact_id]
    lead_by_contact_id: dict[uuid.UUID, Lead] = {}
    if contact_ids:
        leads = (
            await db.execute(
                select(Lead)
                .where(Lead.tenant_id == tenant_id, Lead.contact_id.in_(contact_ids))
                .order_by(Lead.updated_at.desc(), Lead.created_at.desc())
            )
        ).scalars().all()
        for lead in leads:
            lead_by_contact_id.setdefault(lead.contact_id, lead)

    task_by_lead_id: dict[uuid.UUID, Task] = {}
    if lead_by_contact_id:
        ref_to_lead_id = {
            f"intake-dashboard:lead:{lead.id}:follow-up": lead.id
            for lead in lead_by_contact_id.values()
        }
        tasks = (
            await db.execute(
                select(Task)
                .where(
                    Task.tenant_id == tenant_id,
                    Task.external_ref.in_(list(ref_to_lead_id.keys())),
                )
                .order_by(Task.updated_at.desc(), Task.created_at.desc())
            )
        ).scalars().all()
        for task in tasks:
            lead_id = ref_to_lead_id.get(task.external_ref)
            if lead_id:
                task_by_lead_id.setdefault(lead_id, task)

    user_ids = {
        log.created_by_user_id for log, _contact, _creator in log_rows if log.created_by_user_id
    }
    for lead in lead_by_contact_id.values():
        if lead.assigned_to_user_id:
            user_ids.add(lead.assigned_to_user_id)
    for task in task_by_lead_id.values():
        if task.assigned_to_user_id:
            user_ids.add(task.assigned_to_user_id)
    users_by_id: dict[uuid.UUID, User] = {}
    if user_ids:
        users_by_id = {
            user.id: user
            for user in (
                await db.execute(
                    select(User).where(User.tenant_id == tenant_id, User.id.in_(user_ids))
                )
            ).scalars().all()
        }

    export_rows = []
    for log, contact, creator in log_rows:
        lead = lead_by_contact_id.get(log.contact_id) if log.contact_id else None
        task = task_by_lead_id.get(lead.id) if lead else None
        assigned_to_user_id = None
        if task and task.assigned_to_user_id:
            assigned_to_user_id = task.assigned_to_user_id
        elif lead and lead.assigned_to_user_id:
            assigned_to_user_id = lead.assigned_to_user_id
        assigned_to_name = _user_name_from_row(users_by_id.get(assigned_to_user_id))
        logged_by = _user_name_from_row(users_by_id.get(log.created_by_user_id) or creator)
        export_rows.append(
            {
                "call_date": _iso_datetime(log.occurred_at),
                "caller_name": _log_caller_name(log, contact),
                "phone": _log_participant(log, "phone") or (contact.phone if contact else ""),
                "normalized_phone": _log_participant(log, "normalized_phone") or "",
                "practice_area": _log_field_from_body(log, "Practice area") or "",
                "purpose": log.summary or "",
                "notes": _log_field_from_body(log, "Notes") or "",
                "outcome": "lead" if lead else "log_only",
                "lead_status": lead.status if lead else "",
                "tabs3_partner_name": assigned_to_name or "",
                "tabs3_partner_user_id": str(assigned_to_user_id) if assigned_to_user_id else "",
                "assigned_to_name": assigned_to_name or "",
                "assigned_to_user_id": str(assigned_to_user_id) if assigned_to_user_id else "",
                "task_status": task.status if task else "",
                "task_completed_at": _iso_datetime(task.completed_at) if task else "",
                "logged_by_name": logged_by or "",
                "logged_by_user_id": str(log.created_by_user_id) if log.created_by_user_id else "",
                "contact_id": str(log.contact_id) if log.contact_id else "",
                "lead_id": str(lead.id) if lead else "",
                "communication_id": str(log.id),
            }
        )

    range_label = "all" if not start and not end else f"{start or 'start'}_to_{end or 'end'}"
    return _csv_response(export_rows, f"intake-calls-{range_label}.csv")


@router.get("/search", response_model=IntakeDashboardSearchResponse)
async def search_dashboard(
    q: Optional[str] = None,
    phone: Optional[str] = None,
    limit: int = 25,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    query = (q or "").strip()
    normalized_phone = normalize_phone(phone)
    if not query and not normalized_phone:
        raise HTTPException(status_code=422, detail="Provide q or phone")

    results: list[IntakeSearchResult] = []
    name_pattern = f"%{query}%" if query else None
    phone_pattern = f"%{phone.strip()}%" if phone else None

    contact_filters = [Contact.tenant_id == tenant_id, Contact.is_active.is_(True)]
    contact_matchers = []
    if name_pattern:
        contact_matchers.extend(
            [
                Contact.first_name.ilike(name_pattern),
                Contact.last_name.ilike(name_pattern),
                Contact.organization_name.ilike(name_pattern),
                Contact.email.ilike(name_pattern),
            ]
        )
    if normalized_phone:
        normalized_candidates = [normalized_phone]
        if len(normalized_phone) == 10:
            normalized_candidates.append(f"1{normalized_phone}")
        contact_matchers.extend(
            [
                _phone_digits_expr(Contact.phone).in_(normalized_candidates),
                _phone_digits_expr(Contact.secondary_phone).in_(normalized_candidates),
            ]
        )
    elif phone_pattern:
        contact_matchers.extend(
            [Contact.phone.ilike(phone_pattern), Contact.secondary_phone.ilike(phone_pattern)]
        )
    contact_stmt = (
        select(Contact)
        .where(*contact_filters, or_(*contact_matchers))
        .order_by(Contact.updated_at.desc())
        .limit(limit)
    )
    if contact_matchers:
        contacts = (await db.execute(contact_stmt)).scalars().all()
        for contact in contacts:
            name_match = _contact_name_matches(contact, query)
            phone_match = _phone_matches(
                contact.phone,
                contact.secondary_phone,
                normalized_phone=normalized_phone,
            )
            score = _identity_score(
                name_match=name_match,
                phone_match=phone_match,
                name_score=70,
                phone_only_score=55,
                combined_score=90,
            )
            results.append(
                IntakeSearchResult(
                    id=str(contact.id),
                    result_type="contact",
                    title=_contact_title(contact),
                    subtitle=contact.email,
                    phone=contact.phone,
                    normalized_phone=normalize_phone(contact.phone),
                    contact_id=contact.id,
                    score=score,
                    metadata=_match_metadata(
                        name_match=name_match,
                        phone_match=phone_match,
                        extra={"contact_type": contact.contact_type},
                    ),
                )
            )

    lead_matchers = []
    if name_pattern:
        lead_matchers.extend(
            [
                Contact.first_name.ilike(name_pattern),
                Contact.last_name.ilike(name_pattern),
                Contact.organization_name.ilike(name_pattern),
            ]
        )
    if normalized_phone:
        normalized_candidates = [normalized_phone]
        if len(normalized_phone) == 10:
            normalized_candidates.append(f"1{normalized_phone}")
        lead_matchers.extend(
            [
                _phone_digits_expr(Contact.phone).in_(normalized_candidates),
                _phone_digits_expr(Contact.secondary_phone).in_(normalized_candidates),
            ]
        )
    if lead_matchers:
        lead_rows = (
            await db.execute(
                select(Lead, Contact)
                .join(Contact, Contact.id == Lead.contact_id)
                .where(
                    Lead.tenant_id == tenant_id,
                    Contact.tenant_id == tenant_id,
                    or_(*lead_matchers),
                )
                .order_by(Lead.updated_at.desc())
                .limit(limit)
            )
        ).all()
        for lead, contact in lead_rows:
            name_match = _contact_name_matches(contact, query)
            phone_match = _phone_matches(
                contact.phone,
                contact.secondary_phone,
                normalized_phone=normalized_phone,
            )
            score = _identity_score(
                name_match=name_match,
                phone_match=phone_match,
                name_score=75,
                phone_only_score=60,
                combined_score=95,
            )
            results.append(
                IntakeSearchResult(
                    id=str(lead.id),
                    result_type="lead",
                    title=_contact_title(contact),
                    subtitle=f"Lead: {lead.status}",
                    phone=contact.phone,
                    normalized_phone=normalize_phone(contact.phone),
                    practice_area=lead.practice_area,
                    contact_id=contact.id,
                    lead_id=lead.id,
                    occurred_at=lead.created_at,
                    score=score,
                    metadata=_match_metadata(
                        name_match=name_match,
                        phone_match=phone_match,
                        extra={
                            "status": lead.status,
                            "assigned_to_user_id": str(lead.assigned_to_user_id)
                            if lead.assigned_to_user_id
                            else None,
                        },
                    ),
                )
            )

    matter_matchers = []
    if name_pattern:
        matter_matchers.extend(
            [
                Matter.matter_name.ilike(name_pattern),
                Matter.counterparty.ilike(name_pattern),
                Matter.case_number.ilike(name_pattern),
            ]
        )
    if matter_matchers:
        matters = (
            await db.execute(
                select(Matter)
                .where(Matter.tenant_id == tenant_id, or_(*matter_matchers))
                .order_by(Matter.updated_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        for matter in matters:
            prior_user_id = matter.partner_attorney_id or matter.attorney_of_record_id
            attorney_name = await _user_name(db, prior_user_id)
            results.append(
                IntakeSearchResult(
                    id=str(matter.id),
                    result_type="matter",
                    title=matter.matter_name,
                    subtitle=f"Matter: {matter.status}",
                    practice_area=matter.practice_area or matter.matter_type,
                    prior_attorney_name=attorney_name,
                    prior_attorney_user_id=prior_user_id,
                    contact_id=matter.client_contact_id,
                    matter_id=matter.id,
                    occurred_at=matter.created_at,
                    score=80,
                    metadata={
                        "status": matter.status,
                        "case_number": matter.case_number,
                    },
                )
            )

    legacy_matchers = []
    if name_pattern:
        legacy_matchers.append(LegacyCallRecord.caller_name.ilike(name_pattern))
    if normalized_phone:
        legacy_matchers.append(LegacyCallRecord.normalized_phone == normalized_phone)
    if legacy_matchers:
        legacy_rows = (
            await db.execute(
                select(LegacyCallRecord)
                .where(LegacyCallRecord.tenant_id == tenant_id, or_(*legacy_matchers))
                .order_by(LegacyCallRecord.call_date.desc().nullslast())
                .limit(limit)
            )
        ).scalars().all()
        for row in legacy_rows:
            name_match = bool(query and row.caller_name and query.lower() in row.caller_name.lower())
            phone_match = bool(normalized_phone and row.normalized_phone == normalized_phone)
            score = _identity_score(
                name_match=name_match,
                phone_match=phone_match,
                name_score=60,
                phone_only_score=45,
                combined_score=80,
            )
            prior_user = await _resolve_user_by_name(db, tenant_id, row.prior_attorney_name)
            results.append(
                IntakeSearchResult(
                    id=str(row.id),
                    result_type="legacy_call",
                    title=row.caller_name or row.caller_phone or "Legacy caller",
                    subtitle=row.purpose,
                    phone=row.caller_phone,
                    normalized_phone=row.normalized_phone,
                    practice_area=row.practice_area,
                    prior_attorney_name=row.prior_attorney_name,
                    prior_attorney_user_id=prior_user.id if prior_user else None,
                    occurred_at=row.call_date,
                    legacy_call_record_id=row.id,
                    score=score,
                    metadata=_match_metadata(
                        name_match=name_match,
                        phone_match=phone_match,
                        extra={"source_row_id": row.source_row_id},
                    ),
                )
            )

    deduped: dict[tuple[str, str], IntakeSearchResult] = {}
    for item in sorted(results, key=_result_sort_key):
        deduped.setdefault((item.result_type, item.id), item)
    ordered = list(deduped.values())[:limit]
    recommended_item = next(
        (
            item
            for item in ordered
            if item.prior_attorney_name and not item.metadata.get("phone_only_match")
        ),
        None,
    )
    return IntakeDashboardSearchResponse(
        query=query or None,
        phone=phone,
        normalized_phone=normalized_phone,
        history_found=bool(ordered),
        identity_warning=(
            "Phone numbers are caller context only. Shared numbers such as jail, court, "
            "or family phones should not be treated as caller identity without a name/history match."
            if normalized_phone
            else None
        ),
        recommended_attorney_name=(
            recommended_item.prior_attorney_name if recommended_item else None
        ),
        recommended_attorney_user_id=(
            recommended_item.prior_attorney_user_id if recommended_item else None
        ),
        results=ordered,
    )


@router.post("/calls", response_model=IntakeDashboardCallResponse, status_code=201)
async def create_dashboard_call(
    payload: IntakeDashboardCallCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    contact_id = payload.existing_contact_id
    lead_id = payload.existing_lead_id
    created_lead = False
    assignment_task_id = None
    caller_name = payload.caller_name or "Unknown caller"

    if payload.outcome == "create_lead":
        if lead_id:
            lead = (
                await db.execute(
                    select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead not found")
            if payload.assigned_to_user_id:
                lead.assigned_to_user_id = payload.assigned_to_user_id
            if payload.qualified:
                lead.status = "qualified"
            lead_id = lead.id
            contact_id = lead.contact_id
            task = await _upsert_lead_assignment_task(
                db,
                tenant_id=tenant_id,
                lead=lead,
                assigned_to_user_id=lead.assigned_to_user_id,
                created_by_user_id=current_user.id,
            )
            assignment_task_id = task.id if task else None
        else:
            if not contact_id:
                parts = caller_name.split()
                contact = Contact(
                    tenant_id=tenant_id,
                    contact_type="prospect",
                    first_name=parts[0] if parts else caller_name,
                    last_name=" ".join(parts[1:]) if len(parts) > 1 else None,
                    phone=payload.phone,
                    notes=payload.notes,
                    created_by_user_id=current_user.id,
                )
                db.add(contact)
                await db.flush()
                contact_id = contact.id
            lead = Lead(
                tenant_id=tenant_id,
                contact_id=contact_id,
                status="qualified" if payload.qualified else "new",
                source=payload.source or "phone",
                practice_area=payload.practice_area,
                description=payload.purpose,
                assigned_to_user_id=payload.assigned_to_user_id,
                created_by_user_id=current_user.id,
            )
            db.add(lead)
            await db.flush()
            lead_id = lead.id
            created_lead = True
            task = await _upsert_lead_assignment_task(
                db,
                tenant_id=tenant_id,
                lead=lead,
                assigned_to_user_id=lead.assigned_to_user_id,
                created_by_user_id=current_user.id,
            )
            assignment_task_id = task.id if task else None

    occurred_at = payload.occurred_at or datetime.now(timezone.utc)
    body_bits = [
        payload.purpose or "",
        f"Practice area: {payload.practice_area}" if payload.practice_area else "",
        f"Notes: {payload.notes}" if payload.notes else "",
    ]
    log = CommunicationLog(
        tenant_id=tenant_id,
        direction="inbound",
        channel="call",
        status="logged",
        subject=f"Inbound call: {caller_name}",
        body="\n".join(bit for bit in body_bits if bit),
        summary=payload.purpose,
        contact_id=contact_id,
        created_by_user_id=current_user.id,
        occurred_at=occurred_at,
        participants={
            "caller_name": caller_name,
            "phone": payload.phone,
            "normalized_phone": normalize_phone(payload.phone),
        },
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    if assignment_task_id and task:
        await notify_task_created(db, task, str(tenant_id))

    return IntakeDashboardCallResponse(
        communication_id=log.id,
        contact_id=contact_id,
        lead_id=lead_id,
        task_id=assignment_task_id,
        created_lead=created_lead,
        status="logged",
    )


@router.post("/leads/{lead_id}/assign-next", response_model=AssignNextResponse)
async def assign_next_partner(
    lead_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    practice_area = _practice_key(lead.practice_area)
    rule, ordered, active_by_id = await _rotation_rule_with_active_users(
        db, tenant_id, practice_area
    )
    if not rule:
        raise HTTPException(
            status_code=404,
            detail="No practice-specific or firm-wide rotation rule",
        )

    if not rule.eligible_user_ids:
        raise HTTPException(status_code=422, detail="Rotation rule has no eligible users")
    if not ordered:
        raise HTTPException(status_code=422, detail="Rotation rule has no active eligible users")

    if rule.last_assigned_user_id in ordered:
        next_idx = (ordered.index(rule.last_assigned_user_id) + 1) % len(ordered)
    else:
        next_idx = 0
    selected_id = ordered[next_idx]
    selected_user = active_by_id[selected_id]

    lead.assigned_to_user_id = selected_id
    rule.last_assigned_user_id = selected_id
    rule.updated_by_user_id = current_user.id
    task = await _upsert_lead_assignment_task(
        db,
        tenant_id=tenant_id,
        lead=lead,
        assigned_to_user_id=selected_id,
        created_by_user_id=current_user.id,
    )
    await db.commit()
    if task:
        await notify_task_created(db, task, str(tenant_id))

    return AssignNextResponse(
        lead_id=lead.id,
        assigned_to_user_id=selected_id,
        assigned_to_name=selected_user.full_name or selected_user.email,
        practice_area=rule.practice_area,
        rotation_rule_id=rule.id,
        task_id=task.id if task else None,
    )


@router.get("/rotation-rules", response_model=RotationRuleListResponse)
async def list_rotation_rules(
    request: Request,
    admin_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = admin_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    rows = (
        await db.execute(
            select(PartnerRotationState)
            .where(PartnerRotationState.tenant_id == tenant_id)
            .order_by(PartnerRotationState.practice_area.asc())
        )
    ).scalars().all()
    return RotationRuleListResponse(
        rules=[RotationRuleResponse.model_validate(row) for row in rows]
    )


@router.put("/rotation-rules", response_model=RotationRuleListResponse)
async def upsert_rotation_rules(
    payload: RotationRuleUpsertRequest,
    request: Request,
    admin_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = admin_user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    for item in payload.rules:
        practice_area = _practice_key(item.practice_area)
        active_count = (
            await db.execute(
                select(func.count())
                .select_from(User)
                .where(
                    User.tenant_id == tenant_id,
                    User.id.in_(item.eligible_user_ids),
                    User.is_active.is_(True),
                )
            )
        ).scalar_one()
        if active_count != len(set(item.eligible_user_ids)):
            raise HTTPException(
                status_code=422,
                detail=f"Rotation rule '{practice_area}' includes users outside this tenant or inactive users",
            )

        existing = (
            await db.execute(
                select(PartnerRotationState).where(
                    PartnerRotationState.tenant_id == tenant_id,
                    PartnerRotationState.practice_area == practice_area,
                )
            )
        ).scalar_one_or_none()
        user_ids = [str(value) for value in item.eligible_user_ids]
        if existing:
            existing.eligible_user_ids = user_ids
            existing.is_enabled = item.is_enabled
            existing.updated_by_user_id = admin_user.id
        else:
            db.add(
                PartnerRotationState(
                    tenant_id=tenant_id,
                    practice_area=practice_area,
                    eligible_user_ids=user_ids,
                    is_enabled=item.is_enabled,
                    created_by_user_id=admin_user.id,
                    updated_by_user_id=admin_user.id,
                )
            )

    await db.commit()
    return await list_rotation_rules(request, admin_user, db)
