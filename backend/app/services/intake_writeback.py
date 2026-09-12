"""Questionnaire answer write-back: derive, propose, review, apply.

Client portal questionnaires are keyed by question slug, not by template
binding path, so the mapping from an answer to a Contact/Matter field is
built once here from the declared bindings on the client intake form in
``intake_starter_pack`` plus the curated aliases for the starter-pack
question slugs.  Every derived change is a proposal: nothing writes back to a
record until a staff member accepts it, mirroring the propose -> review ->
apply lifecycle of document fact review (``template_fact_review``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.models.conflict_check import ConflictCheckRecord
from app.models.contact import Contact
from app.models.plugin import Matter, MatterEvent
from app.models.task import Task
from app.models.user import User
from app.services.conflict_check import run_conflict_check
from app.services.intake_starter_pack import CLIENT_INTAKE_FORM
from app.services.matter_access import can_access_matter
from app.services.task_workflow import append_task_event, transition_task
from app.services.template_bindings import MANUAL_BINDING

logger = logging.getLogger(__name__)

TASK_KIND = "writeback"
ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")

# Curated aliases for starter-pack questionnaire slugs whose wording clearly
# corresponds to a record field, keyed the same way as the form bindings.
_SLUG_BINDINGS = {
    "other_parties": "matter.counterparty",
}

#: Answers under these keys feed person-name conflict search terms.
CONFLICT_NAME_KEYS = (
    "conflict_spouse",
    "conflict_family",
    "conflict_witnesses",
    "conflict_other",
    "other_parties",
    "matter_counterparty",
)

#: Answers under these keys feed organization-name conflict search terms.
CONFLICT_ORGANIZATION_KEYS = (
    "conflict_businesses",
    "conflict_organizations",
)

#: Resolvable binding path -> record write target. ``max`` caps the proposed
#: value at the destination column width; overlong answers are skipped rather
#: than truncated silently.
FIELD_TARGETS = {
    "client.name": {"entity": "contact", "field": "full_name", "max": 400},
    "client.address.street": {
        "entity": "contact",
        "field": "address.street",
        "max": 300,
    },
    "client.address.city": {"entity": "contact", "field": "address.city", "max": 200},
    "client.address.state": {"entity": "contact", "field": "address.state", "max": 100},
    "client.address.zip": {"entity": "contact", "field": "address.zip", "max": 20},
    "client.phone": {"entity": "contact", "field": "phone", "max": 50},
    "client.email": {"entity": "contact", "field": "email", "max": 300},
    "matter.name": {"entity": "matter", "field": "matter_name", "max": 500},
    "matter.type": {"entity": "matter", "field": "matter_type", "max": 100},
    "matter.description": {"entity": "matter", "field": "description", "max": 5000},
    "matter.role": {"entity": "matter", "field": "role", "max": 100},
    "matter.counterparty": {"entity": "matter", "field": "counterparty", "max": 500},
    "matter.court": {"entity": "matter", "field": "court", "max": 300},
    "matter.case_number": {"entity": "matter", "field": "case_number", "max": 100},
    "matter.judge": {"entity": "matter", "field": "judge", "max": 200},
    "matter.jurisdiction": {"entity": "matter", "field": "jurisdiction", "max": 300},
}


def _question_bindings():
    bindings = dict(_SLUG_BINDINGS)
    for form_field in CLIENT_INTAKE_FORM.fields:
        if form_field.binding == MANUAL_BINDING:
            continue
        if form_field.binding.split(".")[0] not in {"client", "matter"}:
            continue
        if form_field.binding in FIELD_TARGETS:
            bindings.setdefault(form_field.name, form_field.binding)
    return bindings


#: Question slug -> binding path, built from the client intake form's
#: declared bindings (``PackField.name`` matches the questionnaire key
#: pattern, so a form field and a question share one slug) plus curated
#: aliases for starter-pack question slugs.
QUESTION_BINDINGS = _question_bindings()

CONFLICT_QUESTION_KEYS = set(CONFLICT_NAME_KEYS + CONFLICT_ORGANIZATION_KEYS)


def _clean_terms(values):
    out = []
    seen = set()
    for raw in values:
        value = " ".join(str(raw).split())
        if not value or len(value) > 300:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _split_terms(raw):
    parts = []
    for chunk in str(raw or "").replace("\n", ",").split(","):
        value = " ".join(chunk.split())
        if value:
            parts.append(value)
    return parts


def extract_conflict_terms(answers):
    """Person and organization names from the conflict-of-interest answers."""
    names = []
    organizations = []
    for key in CONFLICT_NAME_KEYS:
        for term in _split_terms(answers.get(key)):
            names.append(term)
    for key in CONFLICT_ORGANIZATION_KEYS:
        for term in _split_terms(answers.get(key)):
            organizations.append(term)
    return _clean_terms(names), _clean_terms(organizations)


def _current_value(record, entity, field):
    if field == "full_name":
        parts = [p for p in (record.first_name, record.last_name) if p and p.strip()]
        return " ".join(parts) or None
    if field.startswith("address."):
        address = record.address or {}
        value = address.get(field.split(".", 1)[1])
        return value if value and str(value).strip() else None
    value = getattr(record, field)
    return value if value is not None and str(value).strip() else None


def _apply_value(contact, matter, change):
    entity, field, value = change["entity"], change["field"], change["proposed"]
    record = contact if entity == "contact" else matter
    if field == "full_name":
        parts = value.split()
        record.first_name = " ".join(parts[:-1]) or None
        record.last_name = parts[-1] if len(parts) > 1 else None
    elif field.startswith("address."):
        address = dict(record.address or {})
        address[field.split(".", 1)[1]] = value
        record.address = address
    else:
        setattr(record, field, value)


def _normalized(value):
    return " ".join(str(value or "").split()).casefold()


def derive_changes(questions, answers, contact, matter):
    """Diff mapped answers against the live records.

    An empty record field with an answer present proposes a ``fill``; a
    differing curated value proposes a ``conflict`` that needs a staff
    decision; equal values are skipped.
    """
    changes = []
    for question in questions:
        key = question["key"]
        binding = QUESTION_BINDINGS.get(key)
        target = FIELD_TARGETS.get(binding) if binding else None
        if target is None:
            continue
        answer = " ".join(str(answers.get(key, "")).split())
        if not answer or len(answer) > target["max"]:
            continue
        entity = target["entity"]
        record = contact if entity == "contact" else matter
        current = _current_value(record, entity, target["field"])
        if _normalized(current) == _normalized(answer):
            continue
        changes.append(
            {
                "id": f"{entity}.{target['field']}",
                "entity": entity,
                "field": target["field"],
                "binding": binding,
                "question_key": key,
                "label": question.get("label") or target["field"],
                "current": current or "",
                "proposed": answer,
                "kind": "fill" if current is None else "conflict",
                "status": "pending",
            }
        )
    return changes


def _snapshot_matches(raw_matches):
    snapshot = []
    for raw in raw_matches:
        ids = list(raw.get("matter_ids") or [])
        snapshot.append(
            {
                "contact_id": None
                if raw.get("contact_id") in (None, ZERO_UUID)
                else str(raw["contact_id"]),
                "display_name": str(raw.get("display_name") or "Potential match"),
                "contact_type": raw.get("contact_type"),
                "email": raw.get("email"),
                "match_field": raw.get("match_field"),
                "match_value": raw.get("match_value"),
                "matter_ids": [str(matter_id) for matter_id in ids],
                "matter_names": [str(n) for n in (raw.get("matter_names") or [])],
                "restricted_matter_count": 0,
            }
        )
    return snapshot


async def _record_conflict_check(db, packet, matter, answers):
    names, organizations = extract_conflict_terms(answers)
    if not names and not organizations:
        return None
    # Mirrors routers/plugins.py: a conflict-check failure must never block
    # the flow that triggered it.
    try:
        result = await run_conflict_check(
            db=db,
            tenant_id=packet.tenant_id,
            names=names,
            emails=[],
            organization_names=organizations,
            exclude_matter_ids=[matter.id],
        )
        snapshot = _snapshot_matches(result["matches"])
        record = ConflictCheckRecord(
            id=uuid.uuid4(),
            tenant_id=packet.tenant_id,
            matter_id=matter.id,
            label=f"Intake questionnaire — {matter.matter_name}"[:200],
            query_snapshot={
                "names": names,
                "emails": [],
                "organization_names": organizations,
            },
            result_snapshot=snapshot,
            match_count=len(snapshot),
            restricted_matter_count=0,
            created_by_user_id=packet.created_by,
        )
        db.add(record)
        await db.flush()
        return record
    except Exception:
        logger.exception("Intake conflict check failed for packet %s", packet.id)
        return None


def _task_description(changes, conflict_record):
    lines = [
        "The client's questionnaire proposes updates to contact and matter "
        "records. Review each change and accept or reject it; accepted "
        "changes are applied immediately."
    ]
    for change in changes:
        if change["kind"] == "fill":
            lines.append(f"- Fill {change['label']}: {change['proposed']}")
        else:
            lines.append(
                f"- Resolve {change['label']}: record has "
                f"'{change['current']}', client answered '{change['proposed']}'"
            )
    if conflict_record is not None:
        if conflict_record.match_count:
            lines.append(
                f"Conflict check found {conflict_record.match_count} potential "
                f"match(es); see conflict check {conflict_record.id}."
            )
        else:
            lines.append("Conflict check: no matches.")
    return "\n".join(lines)


async def _resolve_owner_id(db, packet):
    owner = await db.scalar(
        select(User).where(
            User.id == packet.owner_id,
            User.tenant_id == packet.tenant_id,
            User.is_active.is_(True),
        )
    )
    if owner and await can_access_matter(
        db,
        tenant_id=packet.tenant_id,
        user_id=owner.id,
        is_admin=owner.role == "admin",
        matter_id=packet.matter_id,
    ):
        return owner.id
    return None


async def plan_writeback(db, packet, matter, answers):
    """Derive proposals from submitted answers and queue staff review.

    Called once per packet from the questionnaire submit path, inside the
    submit transaction.  Re-submitting the same answers never reaches this
    code: the completed requirement short-circuits the endpoint first.
    """
    contact = await db.get(Contact, packet.contact_id)
    if contact is None:
        return None
    changes = derive_changes(
        packet.config.get("questions", []), answers, contact, matter
    )
    conflict_record = await _record_conflict_check(db, packet, matter, answers)
    state = {
        "changes": changes,
        "conflict_check_id": str(conflict_record.id) if conflict_record else None,
        "derived_at": datetime.now(timezone.utc).isoformat(),
    }
    packet.proposed_changes = state
    if not changes:
        return state
    task_id = uuid.uuid5(packet.id, TASK_KIND)
    task = await db.scalar(
        select(Task)
        .where(Task.id == task_id, Task.tenant_id == packet.tenant_id)
        .with_for_update()
    )
    if task is None:
        task = Task(
            id=task_id,
            tenant_id=packet.tenant_id,
            matter_id=packet.matter_id,
            contact_id=packet.contact_id,
            title=f"Review intake updates: {matter.matter_name}"[:500],
            description=_task_description(changes, conflict_record),
            task_type="review",
            status="pending",
            priority="high",
            assigned_to_user_id=await _resolve_owner_id(db, packet),
            created_by_user_id=packet.created_by,
            source="intake",
            external_ref=f"intake:{packet.id}:{TASK_KIND}",
            pending_action={
                "type": "intake_writeback",
                "matter_id": str(packet.matter_id),
                "packet_id": str(packet.id),
                "conflict_check_id": state["conflict_check_id"],
            },
        )
        db.add(task)
        await db.flush()
        append_task_event(
            db,
            task,
            event_type="created",
            actor_user_id=packet.created_by,
            to_status="pending",
            note=task.title,
        )
    return state


def public_changes(packet):
    state = packet.proposed_changes or {}
    return {
        "matter_id": str(packet.matter_id),
        "changes": state.get("changes", []),
        "conflict_check_id": state.get("conflict_check_id"),
        "derived_at": state.get("derived_at"),
        "pending_count": sum(
            1 for change in state.get("changes", []) if change["status"] == "pending"
        ),
    }


async def decide_changes(
    db, user, packet, *, change_id=None, decide_all=False, accept=True
):
    """Accept or reject individual proposed changes, or the whole set.

    Acceptance re-reads the live record value: when it still matches the
    snapshot the proposal was derived from, the proposed write is applied in
    the caller's transaction; when it already equals the proposed value the
    change is resolved without a write; when someone else changed the field
    in between, the request is refused so staff re-review against live data.
    """
    matter = await db.scalar(
        select(Matter)
        .where(Matter.id == packet.matter_id, Matter.tenant_id == packet.tenant_id)
        .with_for_update(of=Matter)
    )
    if matter is None:
        raise HTTPException(404, "Matter not found")
    contact = await db.scalar(
        select(Contact)
        .where(Contact.id == packet.contact_id, Contact.tenant_id == packet.tenant_id)
        .with_for_update()
    )
    if contact is None:
        raise HTTPException(404, "Client contact not found")
    state = packet.proposed_changes or {}
    changes = list(state.get("changes", []))
    if not decide_all:
        if not change_id or not any(c["id"] == change_id for c in changes):
            raise HTTPException(404, "Proposed change not found")
    else:
        change_id = None
    decided = []
    for change in changes:
        if change["status"] != "pending":
            continue
        if change_id is not None and change["id"] != change_id:
            continue
        record = contact if change["entity"] == "contact" else matter
        live = _current_value(record, change["entity"], change["field"])
        if accept:
            if _normalized(live) != _normalized(change["current"]):
                if _normalized(live) == _normalized(change["proposed"]):
                    change["status"] = "accepted"
                    change["decided_by"] = str(user.id)
                    change["decided_at"] = datetime.now(timezone.utc).isoformat()
                    decided.append(change)
                    continue
                raise HTTPException(
                    409,
                    f"'{change['label']}' changed since the proposal was derived; "
                    "review it against the live record.",
                )
            _apply_value(contact, matter, change)
            change["status"] = "accepted"
        else:
            change["status"] = "rejected"
        change["decided_by"] = str(user.id)
        change["decided_at"] = datetime.now(timezone.utc).isoformat()
        decided.append(change)
    if not decided:
        raise HTTPException(409, "No pending proposed changes matched")
    packet.proposed_changes = {**state, "changes": changes}
    verb = "accepted" if accept else "rejected"
    db.add(
        MatterEvent(
            tenant_id=packet.tenant_id,
            matter_id=packet.matter_id,
            event_type="intake_writeback",
            title=f"Intake updates {verb}",
            content=(
                f"Staff {verb} {len(decided)} intake update(s): "
                + ", ".join(change["label"] for change in decided)
                + "."
            ),
            created_by=user.id,
            metadata_json={
                "decision": verb,
                "changes": [
                    {
                        "id": change["id"],
                        "field": change["field"],
                        "entity": change["entity"],
                        "proposed": change["proposed"],
                        "previous": change["current"],
                    }
                    for change in decided
                ],
            },
        )
    )
    task = await db.scalar(
        select(Task)
        .where(
            Task.id == uuid.uuid5(packet.id, TASK_KIND),
            Task.tenant_id == packet.tenant_id,
        )
        .with_for_update()
    )
    if task is not None:
        append_task_event(
            db,
            task,
            event_type="review_decision",
            actor_user_id=user.id,
            note=f"{verb.capitalize()} {len(decided)} proposed intake update(s)",
            metadata={"change_ids": [change["id"] for change in decided]},
        )
        if not any(change["status"] == "pending" for change in changes):
            transition_task(
                db,
                task,
                to_status="completed",
                actor_user_id=user.id,
                reason="All proposed intake updates reviewed",
            )
    return public_changes(packet)
