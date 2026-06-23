"""Receptionist-focused intake dashboard endpoints."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user, require_admin
from app.models.communication_log import CommunicationLog
from app.models.contact import Contact, Lead
from app.models.intake_dashboard import (
    LegacyCallRecord,
    PartnerAssignmentLog,
    PartnerRotationState,
)
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
    PartnerAssignmentLogEntry,
    PartnerAssignmentLogResponse,
    RecentIntakeCaller,
    RecentIntakeCallersResponse,
    RotationRuleListResponse,
    RotationRuleResponse,
    RotationRuleUpsertRequest,
    ZoomPhoneCallItem,
    ZoomPhoneCallsResponse,
    ZoomPhoneSyncResponse,
)
from app.services.intake_archive_import import normalize_phone
from app.services.task_notifications import notify_task_created
from app.services.zoom_phone import (
    ZoomPhoneIntegrationError,
    sync_zoom_phone_call_history,
)

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

PARTNER_LOG_EXPORT_FIELDS = [
    "created_at",
    "assignment_method",
    "assigned_to_name",
    "assigned_by_name",
    "practice_area",
    "lead_id",
    "contact_id",
    "communication_id",
]


def _zoom_phone_call_item(log: CommunicationLog) -> ZoomPhoneCallItem:
    participants = log.participants or {}
    caller_name = (
        participants.get("caller_name")
        or participants.get("phone")
        or log.subject.replace("Zoom Phone inbound call: ", "").replace(
            "Zoom Phone outbound call: ", ""
        )
        or "Unknown Zoom Phone caller"
    )
    return ZoomPhoneCallItem(
        id=log.id,
        caller_name=caller_name,
        phone=participants.get("phone")
        or participants.get("caller_number")
        or participants.get("callee_number"),
        normalized_phone=participants.get("normalized_phone"),
        direction=participants.get("direction") or log.direction,
        result=participants.get("result"),
        duration_seconds=participants.get("duration_seconds"),
        summary=log.summary,
        transcript_url=participants.get("transcript_url"),
        recording_url=participants.get("recording_url"),
        occurred_at=log.occurred_at,
        contact_id=log.contact_id,
        lead_id=None,
        external_ref=log.external_ref,
    )


async def record_partner_assignment(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_method: str,
    assigned_to_user_id: uuid.UUID | None,
    assigned_to_name: str | None,
    assigned_by_user_id: uuid.UUID | None,
    assigned_by_name: str | None = None,
    lead_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    communication_id: uuid.UUID | None = None,
    practice_area: str | None = None,
    rotation_rule_id: uuid.UUID | None = None,
) -> None:
    """Append an assignment event to the partner log (commits with the caller)."""
    db.add(
        PartnerAssignmentLog(
            tenant_id=tenant_id,
            assignment_method=assignment_method,
            assigned_to_user_id=assigned_to_user_id,
            assigned_to_name=assigned_to_name,
            assigned_by_user_id=assigned_by_user_id,
            assigned_by_name=assigned_by_name,
            lead_id=lead_id,
            contact_id=contact_id,
            communication_id=communication_id,
            practice_area=practice_area,
            rotation_rule_id=rotation_rule_id,
        )
    )


def _practice_key(value: str | None) -> str:
    return (value or "general").strip().lower() or "general"


def _phone_digits_expr(column):
    return func.regexp_replace(func.coalesce(column, ""), "[^0-9]", "", "g")


def _json_text_expr(column, key: str):
    return func.coalesce(column[key].astext, "")


def _contact_title(contact: Contact) -> str:
    return contact.display_name


def _query_tokens(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.strip().lower() for token in query.split() if token.strip()]


def _text_search_condition(fields: list, query: str | None):
    tokens = _query_tokens(query)
    if not tokens:
        return None
    whole = f"%{query.strip()}%"
    whole_match = or_(*(field.ilike(whole) for field in fields))
    token_match = and_(
        *(or_(*(field.ilike(f"%{token}%") for field in fields)) for token in tokens)
    )
    return or_(whole_match, token_match)


def _text_matches(value: str, query: str | None) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return False
    haystack = value.lower()
    return query.lower() in haystack or all(token in haystack for token in tokens)


def _contact_name_matches(contact: Contact, query: str | None) -> bool:
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
    )
    return _text_matches(haystack, query)


def _phone_matches(*values: str | None, normalized_phone: str | None) -> bool:
    if not normalized_phone:
        return False
    candidates = {normalized_phone}
    if len(normalized_phone) == 10:
        candidates.add(f"1{normalized_phone}")
    return any(normalize_phone(value) in candidates for value in values if value)


def _phone_fragment_matches(*values: str | None, phone_fragment: str | None) -> bool:
    if not phone_fragment:
        return False
    return any(
        phone_fragment in (normalize_phone(value) or "") for value in values if value
    )


def _phone_search_conditions(fields: list, phone_digits: str | None) -> list:
    if not phone_digits:
        return []
    candidates = [phone_digits]
    if len(phone_digits) == 10:
        candidates.append(f"1{phone_digits}")
    conditions = []
    for field in fields:
        digits_expr = _phone_digits_expr(field)
        conditions.append(digits_expr.in_(candidates))
        conditions.append(digits_expr.like(f"%{phone_digits}%"))
    return conditions


def _match_metadata(
    *, name_match: bool, phone_match: bool, extra: dict | None = None
) -> dict:
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


async def _resolve_active_user(
    db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID | None
) -> User | None:
    if not user_id:
        return None
    return (
        await db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.id == user_id,
                User.is_active.is_(True),
            )
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
            select(Contact).where(
                Contact.id == lead.contact_id, Contact.tenant_id == tenant_id
            )
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


async def _upsert_general_call_task(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    log: CommunicationLog,
    caller_name: str,
    assigned_to_user_id: uuid.UUID,
    created_by_user_id: uuid.UUID | None,
    title: str | None,
    description: str | None,
    practice_area: str | None,
    purpose: str | None,
    notes: str | None,
    contact_id: uuid.UUID | None,
    lead_id: uuid.UUID | None,
) -> Task:
    task_title = (title or "").strip() or f"Intake task: {caller_name}"
    external_ref = f"intake-dashboard:call:{log.id}:general-task"
    description_bits = [
        "General intake task generated by the local intake dashboard.",
        f"Caller: {caller_name}",
        f"Phone: {_log_participant(log, 'phone')}"
        if _log_participant(log, "phone")
        else "",
        f"Practice area: {practice_area}" if practice_area else "",
        f"Customer reason: {purpose}" if purpose else "",
        f"Reception notes: {notes}" if notes else "",
        f"Task detail: {description}" if description else "",
        f"Linked lead: {lead_id}" if lead_id else "",
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
            title=task_title,
            description="\n".join(bit for bit in description_bits if bit),
            task_type="follow_up",
            status="pending",
            priority="urgent",
            due_date=date.today(),
            contact_id=contact_id,
            assigned_to_user_id=assigned_to_user_id,
            created_by_user_id=created_by_user_id,
            source="intake_dashboard",
            external_ref=external_ref,
        )
        db.add(task)
        await db.flush()
    else:
        task.title = task_title
        task.description = "\n".join(bit for bit in description_bits if bit)
        task.priority = "urgent"
        task.status = "pending" if task.status == "cancelled" else task.status
        task.due_date = task.due_date or date.today()
        task.contact_id = contact_id
        task.assigned_to_user_id = assigned_to_user_id
    return task


async def _assignment_task_for_log(
    db: AsyncSession, tenant_id: uuid.UUID, log: CommunicationLog, lead: Lead | None
) -> Task | None:
    call_task = (
        await db.execute(
            select(Task)
            .where(
                Task.tenant_id == tenant_id,
                Task.external_ref == f"intake-dashboard:call:{log.id}:general-task",
            )
            .order_by(Task.updated_at.desc(), Task.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if call_task:
        return call_task
    return await _assignment_task_for_lead(db, tenant_id, lead)


def _result_sort_key(item: IntakeSearchResult) -> tuple[int, int]:
    type_rank = {"matter": 0, "lead": 1, "contact": 2, "call_log": 3, "legacy_call": 4}
    return (-item.score, type_rank.get(item.result_type, 9))


def _log_participant(log: CommunicationLog, key: str) -> str | None:
    participants = log.participants or {}
    value = participants.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _log_source(log: CommunicationLog) -> str:
    participants = log.participants or {}
    if participants.get("provider") == "zoom_phone" or (
        log.external_ref or ""
    ).startswith("zoom_phone:call:"):
        return "zoom_phone"
    return "manual"


def _log_int(log: CommunicationLog, key: str) -> int | None:
    value = (log.participants or {}).get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
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
        (
            await db.execute(
                select(User).where(
                    User.tenant_id == tenant_id,
                    User.id.in_(eligible_ids),
                    User.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
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
    if limit not in {5, 10, 20, 50}:
        raise HTTPException(status_code=422, detail="Limit must be 5, 10, 20, or 50")

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

    # Batch the enrichment lookups (lead → task → assignee names) into a handful
    # of queries instead of N+1 per row — this endpoint is polled, so per-row
    # round-trips add up. Mirrors the batching in export_call_records.
    contact_ids = [log.contact_id for log, _c, _u in rows if log.contact_id]
    lead_by_contact_id: dict[uuid.UUID, Lead] = {}
    if contact_ids:
        leads = (
            (
                await db.execute(
                    select(Lead)
                    .where(
                        Lead.tenant_id == tenant_id, Lead.contact_id.in_(contact_ids)
                    )
                    .order_by(Lead.updated_at.desc(), Lead.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for lead in leads:
            lead_by_contact_id.setdefault(lead.contact_id, lead)

    call_ref_to_log_id = {
        f"intake-dashboard:call:{log.id}:general-task": log.id for log, _c, _u in rows
    }
    task_by_log_id: dict[uuid.UUID, Task] = {}
    if call_ref_to_log_id:
        tasks = (
            (
                await db.execute(
                    select(Task)
                    .where(
                        Task.tenant_id == tenant_id,
                        Task.external_ref.in_(list(call_ref_to_log_id.keys())),
                    )
                    .order_by(Task.updated_at.desc(), Task.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for task in tasks:
            log_id = call_ref_to_log_id.get(task.external_ref)
            if log_id:
                task_by_log_id.setdefault(log_id, task)

    task_by_lead_id: dict[uuid.UUID, Task] = {}
    if lead_by_contact_id:
        ref_to_lead_id = {
            f"intake-dashboard:lead:{lead.id}:follow-up": lead.id
            for lead in lead_by_contact_id.values()
        }
        tasks = (
            (
                await db.execute(
                    select(Task)
                    .where(
                        Task.tenant_id == tenant_id,
                        Task.external_ref.in_(list(ref_to_lead_id.keys())),
                    )
                    .order_by(Task.updated_at.desc(), Task.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for task in tasks:
            lead_id = ref_to_lead_id.get(task.external_ref)
            if lead_id:
                task_by_lead_id.setdefault(lead_id, task)

    assignee_ids = {
        task.assigned_to_user_id
        for task in (*task_by_log_id.values(), *task_by_lead_id.values())
        if task.assigned_to_user_id
    }
    for lead in lead_by_contact_id.values():
        if lead.assigned_to_user_id:
            assignee_ids.add(lead.assigned_to_user_id)
    assignees_by_id: dict[uuid.UUID, User] = {}
    if assignee_ids:
        assignees_by_id = {
            user.id: user
            for user in (
                await db.execute(
                    select(User).where(
                        User.tenant_id == tenant_id, User.id.in_(assignee_ids)
                    )
                )
            )
            .scalars()
            .all()
        }

    callers = []
    for log, contact, creator in rows:
        lead = lead_by_contact_id.get(log.contact_id) if log.contact_id else None
        task = task_by_log_id.get(log.id) or (
            task_by_lead_id.get(lead.id) if lead else None
        )
        assigned_to_user_id = None
        if task and task.assigned_to_user_id:
            assigned_to_user_id = task.assigned_to_user_id
        elif lead and lead.assigned_to_user_id:
            assigned_to_user_id = lead.assigned_to_user_id
        callers.append(
            RecentIntakeCaller(
                id=log.id,
                caller_name=_log_caller_name(log, contact),
                phone=_log_participant(log, "phone")
                or (contact.phone if contact else None),
                normalized_phone=_log_participant(log, "normalized_phone"),
                practice_area=_log_field_from_body(log, "Practice area"),
                purpose=log.summary,
                notes=_log_field_from_body(log, "Notes"),
                contact_id=log.contact_id,
                lead_id=lead.id if lead else None,
                lead_status=lead.status if lead else None,
                assigned_to_user_id=assigned_to_user_id,
                assigned_to_name=_user_name_from_row(
                    assignees_by_id.get(assigned_to_user_id)
                ),
                task_id=task.id if task else None,
                task_status=task.status if task else None,
                task_priority=task.priority if task else None,
                task_due_date=task.due_date if task else None,
                task_completed_at=task.completed_at if task else None,
                created_by_user_id=log.created_by_user_id,
                created_by_name=_user_name_from_row(creator),
                occurred_at=log.occurred_at,
                source=_log_source(log),
                answered_by=_log_participant(log, "callee_name"),
                result=_log_participant(log, "result"),
                duration_seconds=_log_int(log, "duration_seconds"),
                recording_url=_log_participant(log, "recording_url"),
                transcript_url=_log_participant(log, "transcript_url"),
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
        raise HTTPException(
            status_code=422, detail="start must be before or equal to end"
        )

    filters = [
        CommunicationLog.tenant_id == tenant_id,
        CommunicationLog.channel == "call",
        CommunicationLog.direction == "inbound",
    ]
    if start:
        filters.append(
            CommunicationLog.occurred_at
            >= datetime.combine(start, time.min, tzinfo=timezone.utc)
        )
    if end:
        filters.append(
            CommunicationLog.occurred_at
            <= datetime.combine(end, time.max, tzinfo=timezone.utc)
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

    contact_ids = [
        log.contact_id for log, _contact, _creator in log_rows if log.contact_id
    ]
    lead_by_contact_id: dict[uuid.UUID, Lead] = {}
    if contact_ids:
        leads = (
            (
                await db.execute(
                    select(Lead)
                    .where(
                        Lead.tenant_id == tenant_id, Lead.contact_id.in_(contact_ids)
                    )
                    .order_by(Lead.updated_at.desc(), Lead.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for lead in leads:
            lead_by_contact_id.setdefault(lead.contact_id, lead)

    task_by_lead_id: dict[uuid.UUID, Task] = {}
    if lead_by_contact_id:
        ref_to_lead_id = {
            f"intake-dashboard:lead:{lead.id}:follow-up": lead.id
            for lead in lead_by_contact_id.values()
        }
        tasks = (
            (
                await db.execute(
                    select(Task)
                    .where(
                        Task.tenant_id == tenant_id,
                        Task.external_ref.in_(list(ref_to_lead_id.keys())),
                    )
                    .order_by(Task.updated_at.desc(), Task.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for task in tasks:
            lead_id = ref_to_lead_id.get(task.external_ref)
            if lead_id:
                task_by_lead_id.setdefault(lead_id, task)

    call_ref_to_log_id = {
        f"intake-dashboard:call:{log.id}:general-task": log.id
        for log, _contact, _creator in log_rows
    }
    task_by_log_id: dict[uuid.UUID, Task] = {}
    if call_ref_to_log_id:
        tasks = (
            (
                await db.execute(
                    select(Task)
                    .where(
                        Task.tenant_id == tenant_id,
                        Task.external_ref.in_(list(call_ref_to_log_id.keys())),
                    )
                    .order_by(Task.updated_at.desc(), Task.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for task in tasks:
            log_id = call_ref_to_log_id.get(task.external_ref)
            if log_id:
                task_by_log_id.setdefault(log_id, task)

    user_ids = {
        log.created_by_user_id
        for log, _contact, _creator in log_rows
        if log.created_by_user_id
    }
    for lead in lead_by_contact_id.values():
        if lead.assigned_to_user_id:
            user_ids.add(lead.assigned_to_user_id)
    for task in task_by_lead_id.values():
        if task.assigned_to_user_id:
            user_ids.add(task.assigned_to_user_id)
    for task in task_by_log_id.values():
        if task.assigned_to_user_id:
            user_ids.add(task.assigned_to_user_id)
    users_by_id: dict[uuid.UUID, User] = {}
    if user_ids:
        users_by_id = {
            user.id: user
            for user in (
                await db.execute(
                    select(User).where(
                        User.tenant_id == tenant_id, User.id.in_(user_ids)
                    )
                )
            )
            .scalars()
            .all()
        }

    export_rows = []
    for log, contact, creator in log_rows:
        lead = lead_by_contact_id.get(log.contact_id) if log.contact_id else None
        task = task_by_log_id.get(log.id) or (
            task_by_lead_id.get(lead.id) if lead else None
        )
        assigned_to_user_id = None
        if task and task.assigned_to_user_id:
            assigned_to_user_id = task.assigned_to_user_id
        elif lead and lead.assigned_to_user_id:
            assigned_to_user_id = lead.assigned_to_user_id
        assigned_to_name = _user_name_from_row(users_by_id.get(assigned_to_user_id))
        logged_by = _user_name_from_row(
            users_by_id.get(log.created_by_user_id) or creator
        )
        export_rows.append(
            {
                "call_date": _iso_datetime(log.occurred_at),
                "caller_name": _log_caller_name(log, contact),
                "phone": _log_participant(log, "phone")
                or (contact.phone if contact else ""),
                "normalized_phone": _log_participant(log, "normalized_phone") or "",
                "practice_area": _log_field_from_body(log, "Practice area") or "",
                "purpose": log.summary or "",
                "notes": _log_field_from_body(log, "Notes") or "",
                "outcome": "lead" if lead else "log_only",
                "lead_status": lead.status if lead else "",
                "tabs3_partner_name": assigned_to_name or "",
                "tabs3_partner_user_id": str(assigned_to_user_id)
                if assigned_to_user_id
                else "",
                "assigned_to_name": assigned_to_name or "",
                "assigned_to_user_id": str(assigned_to_user_id)
                if assigned_to_user_id
                else "",
                "task_status": task.status if task else "",
                "task_completed_at": _iso_datetime(task.completed_at) if task else "",
                "logged_by_name": logged_by or "",
                "logged_by_user_id": str(log.created_by_user_id)
                if log.created_by_user_id
                else "",
                "contact_id": str(log.contact_id) if log.contact_id else "",
                "lead_id": str(lead.id) if lead else "",
                "communication_id": str(log.id),
            }
        )

    range_label = (
        "all" if not start and not end else f"{start or 'start'}_to_{end or 'end'}"
    )
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
    query_phone_digits = normalize_phone(query)
    phone_digits = normalized_phone or query_phone_digits
    phone_pattern = f"%{phone.strip()}%" if phone else None

    contact_filters = [Contact.tenant_id == tenant_id, Contact.is_active.is_(True)]
    contact_matchers = []
    contact_text_condition = _text_search_condition(
        [
            Contact.first_name,
            Contact.last_name,
            Contact.organization_name,
            Contact.email,
        ],
        query,
    )
    if contact_text_condition is not None:
        contact_matchers.append(contact_text_condition)
    contact_matchers.extend(
        _phone_search_conditions([Contact.phone, Contact.secondary_phone], phone_digits)
    )
    if phone_pattern and not phone_digits:
        contact_matchers.extend(
            [
                Contact.phone.ilike(phone_pattern),
                Contact.secondary_phone.ilike(phone_pattern),
            ]
        )
    if contact_matchers:
        contact_stmt = (
            select(Contact)
            .where(*contact_filters, or_(*contact_matchers))
            .order_by(Contact.updated_at.desc())
            .limit(limit)
        )
        contacts = (await db.execute(contact_stmt)).scalars().all()
        for contact in contacts:
            name_match = _contact_name_matches(contact, query)
            phone_match = _phone_matches(
                contact.phone,
                contact.secondary_phone,
                normalized_phone=phone_digits,
            ) or _phone_fragment_matches(
                contact.phone,
                contact.secondary_phone,
                phone_fragment=phone_digits,
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
    lead_text_condition = _text_search_condition(
        [Contact.first_name, Contact.last_name, Contact.organization_name],
        query,
    )
    if lead_text_condition is not None:
        lead_matchers.append(lead_text_condition)
    lead_matchers.extend(
        _phone_search_conditions([Contact.phone, Contact.secondary_phone], phone_digits)
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
                normalized_phone=phone_digits,
            ) or _phone_fragment_matches(
                contact.phone,
                contact.secondary_phone,
                phone_fragment=phone_digits,
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
    matter_text_condition = _text_search_condition(
        [Matter.matter_name, Matter.counterparty, Matter.case_number],
        query,
    )
    if matter_text_condition is not None:
        matter_matchers.append(matter_text_condition)
    if matter_matchers:
        matters = (
            (
                await db.execute(
                    select(Matter)
                    .where(Matter.tenant_id == tenant_id, or_(*matter_matchers))
                    .order_by(Matter.updated_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
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
    legacy_text_condition = _text_search_condition(
        [
            LegacyCallRecord.caller_name,
            LegacyCallRecord.purpose,
            LegacyCallRecord.notes,
            LegacyCallRecord.prior_attorney_name,
        ],
        query,
    )
    if legacy_text_condition is not None:
        legacy_matchers.append(legacy_text_condition)
    if phone_digits:
        legacy_matchers.append(
            LegacyCallRecord.normalized_phone.like(f"%{phone_digits}%")
        )
    if legacy_matchers:
        legacy_rows = (
            (
                await db.execute(
                    select(LegacyCallRecord)
                    .where(
                        LegacyCallRecord.tenant_id == tenant_id, or_(*legacy_matchers)
                    )
                    .order_by(LegacyCallRecord.call_date.desc().nullslast())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for row in legacy_rows:
            name_match = _text_matches(
                " ".join(
                    value
                    for value in [
                        row.caller_name,
                        row.purpose,
                        row.notes,
                        row.prior_attorney_name,
                    ]
                    if value
                ),
                query,
            )
            phone_match = bool(
                phone_digits
                and row.normalized_phone
                and phone_digits in row.normalized_phone
            )
            score = _identity_score(
                name_match=name_match,
                phone_match=phone_match,
                name_score=60,
                phone_only_score=45,
                combined_score=80,
            )
            prior_user = await _resolve_user_by_name(
                db, tenant_id, row.prior_attorney_name
            )
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

    log_matchers = []
    caller_name_expr = _json_text_expr(CommunicationLog.participants, "caller_name")
    caller_phone_expr = _json_text_expr(CommunicationLog.participants, "phone")
    normalized_log_phone_expr = _json_text_expr(
        CommunicationLog.participants, "normalized_phone"
    )
    log_text_condition = _text_search_condition(
        [
            CommunicationLog.subject,
            CommunicationLog.summary,
            CommunicationLog.body,
            caller_name_expr,
        ],
        query,
    )
    if log_text_condition is not None:
        log_matchers.append(log_text_condition)
    if phone_digits:
        log_matchers.extend(
            [
                _phone_digits_expr(caller_phone_expr).like(f"%{phone_digits}%"),
                normalized_log_phone_expr.like(f"%{phone_digits}%"),
            ]
        )
    if log_matchers:
        log_rows = (
            await db.execute(
                select(CommunicationLog, Contact)
                .outerjoin(
                    Contact,
                    (Contact.id == CommunicationLog.contact_id)
                    & (Contact.tenant_id == tenant_id),
                )
                .where(
                    CommunicationLog.tenant_id == tenant_id,
                    CommunicationLog.channel == "call",
                    CommunicationLog.direction == "inbound",
                    or_(*log_matchers),
                )
                .order_by(CommunicationLog.occurred_at.desc())
                .limit(limit)
            )
        ).all()
        for log, contact in log_rows:
            caller_name = _log_caller_name(log, contact)
            log_phone = _log_participant(log, "phone") or (
                contact.phone if contact else None
            )
            log_normalized_phone = _log_participant(
                log, "normalized_phone"
            ) or normalize_phone(log_phone)
            name_match = _text_matches(
                " ".join(
                    value
                    for value in [
                        caller_name,
                        log.subject,
                        log.summary,
                        log.body,
                    ]
                    if value
                ),
                query,
            )
            phone_match = bool(
                phone_digits
                and (
                    _phone_fragment_matches(log_phone, phone_fragment=phone_digits)
                    or (log_normalized_phone and phone_digits in log_normalized_phone)
                )
            )
            score = _identity_score(
                name_match=name_match,
                phone_match=phone_match,
                name_score=62,
                phone_only_score=42,
                combined_score=78,
            )
            results.append(
                IntakeSearchResult(
                    id=str(log.id),
                    result_type="call_log",
                    title=caller_name,
                    subtitle=log.summary,
                    phone=log_phone,
                    normalized_phone=log_normalized_phone,
                    practice_area=_log_field_from_body(log, "Practice area"),
                    contact_id=log.contact_id,
                    occurred_at=log.occurred_at,
                    answered_by=_log_participant(log, "callee_name"),
                    result=_log_participant(log, "result"),
                    score=score,
                    metadata=_match_metadata(
                        name_match=name_match,
                        phone_match=phone_match,
                        extra={
                            "status": log.status,
                            "notes": _log_field_from_body(log, "Notes"),
                        },
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
        normalized_phone=phone_digits,
        history_found=bool(ordered),
        identity_warning=(
            "Phone numbers are caller context only. Shared numbers such as jail, court, "
            "or family phones should not be treated as caller identity without a name/history match."
            if phone_digits
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


@router.get("/zoom-phone/calls", response_model=ZoomPhoneCallsResponse)
async def list_zoom_phone_calls(
    limit: int = Query(25, ge=1, le=100),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))

    result = await db.execute(
        select(CommunicationLog)
        .where(
            CommunicationLog.tenant_id == tenant_id,
            CommunicationLog.channel == "call",
            CommunicationLog.direction == "inbound",
            or_(
                CommunicationLog.external_ref.like("zoom_phone:call:%"),
                CommunicationLog.participants["provider"].astext == "zoom_phone",
            ),
        )
        .order_by(CommunicationLog.occurred_at.desc())
        .limit(limit)
    )
    return ZoomPhoneCallsResponse(
        calls=[_zoom_phone_call_item(log) for log in result.scalars().all()]
    )


@router.post("/zoom-phone/sync", response_model=ZoomPhoneSyncResponse)
async def sync_zoom_phone_calls(
    days: int = Query(7, ge=1, le=31),
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    try:
        result = await sync_zoom_phone_call_history(
            db, tenant_id=str(tenant_id), days=days
        )
    except ZoomPhoneIntegrationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return ZoomPhoneSyncResponse(
        imported=result.imported,
        updated=result.updated,
        skipped=result.skipped,
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
    task = None
    caller_name = payload.caller_name or "Unknown caller"
    source_log = None
    if payload.existing_communication_id:
        source_log = (
            await db.execute(
                select(CommunicationLog).where(
                    CommunicationLog.id == payload.existing_communication_id,
                    CommunicationLog.tenant_id == tenant_id,
                    CommunicationLog.channel == "call",
                )
            )
        ).scalar_one_or_none()
        if not source_log:
            raise HTTPException(status_code=404, detail="Source call record not found")
    lead_assignee = (
        await _resolve_active_user(db, tenant_id, payload.assigned_to_user_id)
        if payload.task_mode == "partner_rotation"
        else None
    )
    if (
        payload.task_mode == "partner_rotation"
        and payload.assigned_to_user_id
        and not lead_assignee
    ):
        raise HTTPException(status_code=404, detail="Assigned user not found")

    general_task_assignee = None
    if payload.task_mode == "specific_staff":
        general_task_assignee = await _resolve_active_user(
            db, tenant_id, payload.task_assigned_to_user_id
        )
        if not general_task_assignee:
            raise HTTPException(
                status_code=422, detail="Select an active staff member for the task"
            )

    if payload.outcome == "create_lead":
        if lead_id:
            lead = (
                await db.execute(
                    select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead not found")
            if lead_assignee:
                lead.assigned_to_user_id = lead_assignee.id
            if payload.qualified:
                lead.status = "qualified"
            lead_id = lead.id
            contact_id = lead.contact_id
            if payload.task_mode == "partner_rotation":
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
                assigned_to_user_id=lead_assignee.id if lead_assignee else None,
                created_by_user_id=current_user.id,
            )
            db.add(lead)
            await db.flush()
            lead_id = lead.id
            created_lead = True
            if payload.task_mode == "partner_rotation":
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
    body = "\n".join(bit for bit in body_bits if bit)
    participants = {
        "caller_name": caller_name,
        "phone": payload.phone,
        "normalized_phone": normalize_phone(payload.phone),
    }
    if source_log:
        existing_participants = source_log.participants or {}
        participants = {
            **existing_participants,
            **{key: value for key, value in participants.items() if value is not None},
        }
        if source_log.body and body:
            body = f"{body}\n\n--- Original Zoom Phone details ---\n{source_log.body}"
        elif source_log.body:
            body = source_log.body
        log = source_log
        log.direction = source_log.direction or "inbound"
        log.status = "logged"
        log.subject = f"Inbound call: {caller_name}"
        log.body = body
        log.summary = payload.purpose or source_log.summary
        log.contact_id = contact_id
        log.created_by_user_id = current_user.id
        log.occurred_at = payload.occurred_at or source_log.occurred_at or occurred_at
        log.participants = participants
    else:
        log = CommunicationLog(
            tenant_id=tenant_id,
            direction="inbound",
            channel="call",
            status="logged",
            subject=f"Inbound call: {caller_name}",
            body=body,
            summary=payload.purpose,
            contact_id=contact_id,
            created_by_user_id=current_user.id,
            occurred_at=occurred_at,
            participants=participants,
        )
        db.add(log)
    await db.flush()
    if payload.task_mode == "specific_staff" and general_task_assignee:
        task = await _upsert_general_call_task(
            db,
            tenant_id=tenant_id,
            log=log,
            caller_name=caller_name,
            assigned_to_user_id=general_task_assignee.id,
            created_by_user_id=current_user.id,
            title=payload.task_title,
            description=payload.task_description,
            practice_area=payload.practice_area,
            purpose=payload.purpose,
            notes=payload.notes,
            contact_id=contact_id,
            lead_id=lead_id,
        )
        assignment_task_id = task.id
        await record_partner_assignment(
            db,
            tenant_id=tenant_id,
            assignment_method="specific_staff",
            assigned_to_user_id=general_task_assignee.id,
            assigned_to_name=general_task_assignee.full_name
            or general_task_assignee.email,
            assigned_by_user_id=current_user.id,
            assigned_by_name=current_user.full_name or current_user.email,
            lead_id=lead_id,
            contact_id=contact_id,
            communication_id=log.id,
            practice_area=payload.practice_area,
        )
    elif payload.task_mode == "partner_rotation" and lead_assignee:
        await record_partner_assignment(
            db,
            tenant_id=tenant_id,
            assignment_method="prior_attorney",
            assigned_to_user_id=lead_assignee.id,
            assigned_to_name=lead_assignee.full_name or lead_assignee.email,
            assigned_by_user_id=current_user.id,
            assigned_by_name=current_user.full_name or current_user.email,
            lead_id=lead_id,
            contact_id=contact_id,
            communication_id=log.id,
            practice_area=payload.practice_area,
        )
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
        await db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
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
        raise HTTPException(
            status_code=422, detail="Rotation rule has no eligible users"
        )
    if not ordered:
        raise HTTPException(
            status_code=422, detail="Rotation rule has no active eligible users"
        )

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
    await record_partner_assignment(
        db,
        tenant_id=tenant_id,
        assignment_method="partner_rotation",
        assigned_to_user_id=selected_id,
        assigned_to_name=selected_user.full_name or selected_user.email,
        assigned_by_user_id=current_user.id,
        assigned_by_name=current_user.full_name or current_user.email,
        lead_id=lead.id,
        contact_id=lead.contact_id,
        practice_area=rule.practice_area,
        rotation_rule_id=rule.id,
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
        (
            await db.execute(
                select(PartnerRotationState)
                .where(PartnerRotationState.tenant_id == tenant_id)
                .order_by(PartnerRotationState.practice_area.asc())
            )
        )
        .scalars()
        .all()
    )
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


@router.get("/partner-log", response_model=PartnerAssignmentLogResponse)
async def list_partner_log(
    start: date | None = Query(None, description="Inclusive start date"),
    end: date | None = Query(None, description="Inclusive end date"),
    assigned_to_user_id: uuid.UUID | None = Query(None),
    limit: int = 200,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    if start and end and start > end:
        raise HTTPException(
            status_code=422, detail="start must be before or equal to end"
        )
    filters = [PartnerAssignmentLog.tenant_id == tenant_id]
    if start:
        filters.append(
            PartnerAssignmentLog.created_at
            >= datetime.combine(start, time.min, tzinfo=timezone.utc)
        )
    if end:
        filters.append(
            PartnerAssignmentLog.created_at
            <= datetime.combine(end, time.max, tzinfo=timezone.utc)
        )
    if assigned_to_user_id:
        filters.append(PartnerAssignmentLog.assigned_to_user_id == assigned_to_user_id)
    rows = (
        (
            await db.execute(
                select(PartnerAssignmentLog)
                .where(*filters)
                .order_by(PartnerAssignmentLog.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return PartnerAssignmentLogResponse(
        entries=[PartnerAssignmentLogEntry.model_validate(row) for row in rows]
    )


@router.get("/partner-log/export")
async def export_partner_log(
    start: date | None = Query(None, description="Inclusive start date"),
    end: date | None = Query(None, description="Inclusive end date"),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    if start and end and start > end:
        raise HTTPException(
            status_code=422, detail="start must be before or equal to end"
        )
    filters = [PartnerAssignmentLog.tenant_id == tenant_id]
    if start:
        filters.append(
            PartnerAssignmentLog.created_at
            >= datetime.combine(start, time.min, tzinfo=timezone.utc)
        )
    if end:
        filters.append(
            PartnerAssignmentLog.created_at
            <= datetime.combine(end, time.max, tzinfo=timezone.utc)
        )
    rows = (
        (
            await db.execute(
                select(PartnerAssignmentLog)
                .where(*filters)
                .order_by(PartnerAssignmentLog.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=PARTNER_LOG_EXPORT_FIELDS)
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {
                "created_at": _iso_datetime(r.created_at),
                "assignment_method": r.assignment_method,
                "assigned_to_name": r.assigned_to_name or "",
                "assigned_by_name": r.assigned_by_name or "",
                "practice_area": r.practice_area or "",
                "lead_id": str(r.lead_id) if r.lead_id else "",
                "contact_id": str(r.contact_id) if r.contact_id else "",
                "communication_id": str(r.communication_id)
                if r.communication_id
                else "",
            }
        )
    buffer.seek(0)
    range_label = (
        "all" if not start and not end else f"{start or 'start'}_to_{end or 'end'}"
    )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=partner-log-{range_label}.csv"
        },
    )
