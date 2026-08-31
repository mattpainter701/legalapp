"""Deterministic execution helpers for bounded matter workflow templates."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.configurable_workflow import (
    CustomFieldDefinition,
    MatterCustomFieldValue,
    MatterWorkflowChecklistDefinition,
    MatterWorkflowFieldRequirement,
    MatterWorkflowRun,
    MatterWorkflowRunEvent,
    MatterWorkflowRunStep,
    MatterWorkflowStageDefinition,
    MatterWorkflowTemplate,
    MatterWorkflowTemplateVersion,
)
from app.models.plugin import Matter
from app.models.task import Task
from app.models.user import User
from app.schemas.configurable_workflow import WorkflowDefinitionInput
from app.services.task_workflow import append_task_event, transition_task


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def digest_payload(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def value_hmac(value: Any) -> str:
    secret = get_settings().SECRET_KEY.encode("utf-8")
    return hmac.new(
        secret, canonical_json(value).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def definition_payload(body: WorkflowDefinitionInput) -> dict[str, Any]:
    return {
        "initial_stage_key": body.initial_stage_key,
        "stages": [
            {"stage_key": stage.stage_key, "label": stage.label}
            for stage in body.stages
        ],
        "checklist": [
            {
                "item_key": item.item_key,
                "stage_key": item.stage_key,
                "title": item.title,
                "description": item.description,
                "task_type": item.task_type,
                "priority": item.priority,
                "due_offset_days": item.due_offset_days,
                "assignee_role": item.assignee_role,
            }
            for item in body.checklist
        ],
        "required_field_definition_ids": sorted(
            str(field_id) for field_id in body.required_field_definition_ids
        ),
    }


async def load_template_bundle(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    lock: bool = False,
) -> tuple[
    MatterWorkflowTemplate,
    MatterWorkflowTemplateVersion,
    list[MatterWorkflowStageDefinition],
    list[MatterWorkflowChecklistDefinition],
    list[CustomFieldDefinition],
]:
    query = (
        select(MatterWorkflowTemplateVersion, MatterWorkflowTemplate)
        .join(
            MatterWorkflowTemplate,
            MatterWorkflowTemplate.id == MatterWorkflowTemplateVersion.template_id,
        )
        .where(
            MatterWorkflowTemplateVersion.id == version_id,
            MatterWorkflowTemplateVersion.tenant_id == tenant_id,
            MatterWorkflowTemplate.tenant_id == tenant_id,
        )
    )
    if lock:
        query = query.with_for_update(
            of=(MatterWorkflowTemplateVersion, MatterWorkflowTemplate)
        )
    row = (await db.execute(query)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Workflow template not found")
    version, template = row
    stages = (
        (
            await db.execute(
                select(MatterWorkflowStageDefinition)
                .where(
                    MatterWorkflowStageDefinition.tenant_id == tenant_id,
                    MatterWorkflowStageDefinition.template_version_id == version.id,
                )
                .order_by(MatterWorkflowStageDefinition.position)
            )
        )
        .scalars()
        .all()
    )
    checklist = (
        (
            await db.execute(
                select(MatterWorkflowChecklistDefinition)
                .where(
                    MatterWorkflowChecklistDefinition.tenant_id == tenant_id,
                    MatterWorkflowChecklistDefinition.template_version_id == version.id,
                )
                .order_by(MatterWorkflowChecklistDefinition.position)
            )
        )
        .scalars()
        .all()
    )
    fields = (
        (
            await db.execute(
                select(CustomFieldDefinition)
                .join(
                    MatterWorkflowFieldRequirement,
                    MatterWorkflowFieldRequirement.field_definition_id
                    == CustomFieldDefinition.id,
                )
                .where(
                    MatterWorkflowFieldRequirement.tenant_id == tenant_id,
                    MatterWorkflowFieldRequirement.template_version_id == version.id,
                    CustomFieldDefinition.tenant_id == tenant_id,
                )
                .order_by(CustomFieldDefinition.field_key)
            )
        )
        .scalars()
        .all()
    )
    return template, version, list(stages), list(checklist), list(fields)


def stored_definition_payload(
    version: MatterWorkflowTemplateVersion,
    stages: list[MatterWorkflowStageDefinition],
    checklist: list[MatterWorkflowChecklistDefinition],
    fields: list[CustomFieldDefinition],
) -> dict[str, Any]:
    return {
        "initial_stage_key": version.initial_stage_key,
        "stages": [
            {"stage_key": stage.stage_key, "label": stage.label} for stage in stages
        ],
        "checklist": [
            {
                "item_key": item.item_key,
                "stage_key": item.stage_key,
                "title": item.title,
                "description": item.description,
                "task_type": item.task_type,
                "priority": item.priority,
                "due_offset_days": item.due_offset_days,
                "assignee_role": item.assignee_role,
            }
            for item in checklist
        ],
        "required_field_definition_ids": sorted(str(field.id) for field in fields),
    }


async def matter_snapshot(
    db: AsyncSession,
    matter: Matter,
) -> tuple[dict[str, Any], dict[uuid.UUID, MatterCustomFieldValue]]:
    rows = (
        await db.execute(
            select(CustomFieldDefinition, MatterCustomFieldValue)
            .outerjoin(
                MatterCustomFieldValue,
                (MatterCustomFieldValue.field_definition_id == CustomFieldDefinition.id)
                & (MatterCustomFieldValue.tenant_id == CustomFieldDefinition.tenant_id)
                & (MatterCustomFieldValue.matter_id == matter.id),
            )
            .where(
                CustomFieldDefinition.tenant_id == matter.tenant_id,
                CustomFieldDefinition.entity_type == "matter",
                CustomFieldDefinition.active.is_(True),
            )
            .order_by(CustomFieldDefinition.field_key)
        )
    ).all()
    values: dict[uuid.UUID, MatterCustomFieldValue] = {}
    field_evidence: list[dict[str, Any]] = []
    for field, value in rows:
        if value is not None:
            values[field.id] = value
        field_evidence.append(
            {
                "field_definition_id": str(field.id),
                "field_key": field.field_key,
                "schema_version": field.schema_version,
                "sensitive": field.sensitive,
                "present": value is not None,
                # Values never enter a preview or durable run snapshot. HMAC is
                # sufficient to reject a changed preview without leaking PII.
                "value_hmac": value.value_hmac if value is not None else None,
            }
        )
    return (
        {
            "matter_id": str(matter.id),
            "matter_type": matter.matter_type,
            "practice_area": matter.practice_area,
            "stage": matter.stage,
            "matter_owner_user_id": str(matter.user_id),
            "attorney_of_record_id": (
                str(matter.attorney_of_record_id)
                if matter.attorney_of_record_id
                else None
            ),
            "custom_fields": field_evidence,
        },
        values,
    )


def _value_present(value: MatterCustomFieldValue | None) -> bool:
    if value is None:
        return False
    return value.value_json not in (None, "", [])


def _assignee_preview(
    item: MatterWorkflowChecklistDefinition, matter: Matter
) -> str | None:
    if item.assignee_role == "matter_owner":
        return str(matter.user_id)
    if item.assignee_role == "attorney_of_record":
        return (
            str(matter.attorney_of_record_id) if matter.attorney_of_record_id else None
        )
    if item.assignee_role == "template_applier":
        return "approval_actor"
    return None


async def build_preview(
    db: AsyncSession,
    *,
    matter: Matter,
    version_id: uuid.UUID,
    as_of: date,
) -> tuple[dict[str, Any], str, str, str]:
    template, version, stages, checklist, required_fields = await load_template_bundle(
        db, matter.tenant_id, version_id
    )
    if not template.active or version.status != "approved":
        raise HTTPException(
            status_code=409, detail="Workflow template is not active and approved"
        )
    expected_definition = stored_definition_payload(
        version, stages, checklist, required_fields
    )
    definition_sha256 = digest_payload(expected_definition)
    if definition_sha256 != version.definition_sha256:
        raise HTTPException(
            status_code=409,
            detail="Workflow template definition no longer matches its approval",
        )
    stage_by_key = {stage.stage_key: stage for stage in stages}
    initial = stage_by_key.get(version.initial_stage_key)
    if initial is None:
        raise HTTPException(
            status_code=409, detail="Workflow template has no valid initial stage"
        )
    matter_evidence, values = await matter_snapshot(db, matter)
    missing = [
        {
            "field_definition_id": str(field.id),
            "field_key": field.field_key,
            "label": field.label,
            "sensitive": field.sensitive,
        }
        for field in required_fields
        if not field.active or not _value_present(values.get(field.id))
    ]
    tasks = [
        {
            "item_key": item.item_key,
            "stage_key": item.stage_key,
            "stage_label": stage_by_key[item.stage_key].label,
            "title": item.title,
            "description": item.description,
            "task_type": item.task_type,
            "priority": item.priority,
            "due_date": (as_of + timedelta(days=item.due_offset_days)).isoformat(),
            "due_offset_days": item.due_offset_days,
            "assignee_role": item.assignee_role,
            "assigned_to_user_id": _assignee_preview(item, matter),
        }
        for item in checklist
    ]
    missing_assignees = [
        {
            "item_key": item["item_key"],
            "title": item["title"],
            "assignee_role": item["assignee_role"],
        }
        for item in tasks
        if item["assignee_role"] == "attorney_of_record"
        and item["assigned_to_user_id"] is None
    ]
    matter_sha256 = digest_payload(matter_evidence)
    preview = {
        "template_id": str(template.id),
        "template_version_id": str(version.id),
        "template_name": template.name,
        "template_version": version.version,
        "template_sha256": definition_sha256,
        "matter_sha256": matter_sha256,
        "as_of": as_of.isoformat(),
        "initial_stage": {"stage_key": initial.stage_key, "label": initial.label},
        "tasks": tasks,
        "missing_required_fields": missing,
        "missing_assignees": missing_assignees,
        "can_apply": not missing and not missing_assignees,
    }
    return preview, digest_payload(preview), definition_sha256, matter_sha256


async def append_run_event(
    db: AsyncSession,
    run: MatterWorkflowRun,
    *,
    event_type: str,
    actor_user_id: uuid.UUID,
    detail: dict[str, Any] | None = None,
) -> MatterWorkflowRunEvent:
    sequence = await db.scalar(
        select(func.coalesce(func.max(MatterWorkflowRunEvent.sequence), 0) + 1).where(
            MatterWorkflowRunEvent.tenant_id == run.tenant_id,
            MatterWorkflowRunEvent.run_id == run.id,
        )
    )
    payload = {
        "run_id": str(run.id),
        "sequence": int(sequence or 1),
        "event_type": event_type,
        "actor_user_id": str(actor_user_id),
        "detail": detail or {},
    }
    event = MatterWorkflowRunEvent(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=int(sequence or 1),
        event_type=event_type,
        actor_user_id=actor_user_id,
        detail_json=detail or {},
        evidence_sha256=digest_payload(payload),
    )
    db.add(event)
    # Production sessions disable autoflush. Persist each append before the
    # next MAX(sequence) allocation so a multi-event transition cannot reuse
    # the same sequence inside one transaction. Callers retain transaction
    # ownership, so a later failure still rolls the evidence row back.
    await db.flush()
    return event


async def append_run_step(
    db: AsyncSession,
    run: MatterWorkflowRun,
    *,
    step_type: str,
    action_key: str,
    status: str,
    task_id: uuid.UUID | None = None,
    evidence: dict[str, Any] | None = None,
) -> MatterWorkflowRunStep:
    sequence = await db.scalar(
        select(func.coalesce(func.max(MatterWorkflowRunStep.sequence), 0) + 1).where(
            MatterWorkflowRunStep.tenant_id == run.tenant_id,
            MatterWorkflowRunStep.run_id == run.id,
        )
    )
    payload = {
        "run_id": str(run.id),
        "sequence": int(sequence or 1),
        "step_type": step_type,
        "action_key": action_key,
        "status": status,
        "task_id": str(task_id) if task_id else None,
        "evidence": evidence or {},
    }
    step = MatterWorkflowRunStep(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=int(sequence or 1),
        step_type=step_type,
        action_key=action_key,
        status=status,
        task_id=task_id,
        evidence_json=evidence or {},
        evidence_sha256=digest_payload(payload),
    )
    db.add(step)
    await db.flush()
    return step


async def _active_same_tenant_user(
    db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    found = await db.scalar(
        select(User.id).where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )
    return found is not None


async def apply_run(
    db: AsyncSession,
    *,
    run: MatterWorkflowRun,
    matter: Matter,
    actor_user_id: uuid.UUID,
    preview_sha256: str,
) -> MatterWorkflowRun:
    if run.status == "applied":
        if run.preview_sha256 != preview_sha256:
            raise HTTPException(
                status_code=409, detail="The applied run has different preview evidence"
            )
        return run
    if run.status != "planned":
        raise HTTPException(status_code=409, detail=f"Workflow run is {run.status}")
    if run.preview_sha256 != preview_sha256:
        raise HTTPException(
            status_code=409, detail="Preview evidence does not match this run"
        )
    (
        current_preview,
        current_preview_sha,
        template_sha,
        matter_sha,
    ) = await build_preview(
        db,
        matter=matter,
        version_id=run.template_version_id,
        as_of=date.fromisoformat(run.preview_json["as_of"]),
    )
    if (
        template_sha != run.template_sha256
        or matter_sha != run.matter_sha256
        or current_preview_sha != run.preview_sha256
    ):
        raise HTTPException(
            status_code=409,
            detail="Matter data or workflow template changed after preview; preview again",
        )
    if not current_preview["can_apply"]:
        raise HTTPException(
            status_code=409,
            detail="Preview has unresolved required fields or assignees",
        )

    run.approved_by_user_id = actor_user_id
    run.approved_at = datetime.now(timezone.utc)
    # Mark the in-transaction target state before any task flush. PostgreSQL's
    # approval invariant rejects a planned row carrying approval metadata;
    # the surrounding transaction still rolls the status back on any failure.
    run.status = "applied"
    await append_run_event(
        db,
        run,
        event_type="approved",
        actor_user_id=actor_user_id,
        detail={"preview_sha256": run.preview_sha256},
    )

    initial_stage = current_preview["initial_stage"]
    matter.stage = initial_stage["label"]
    await append_run_step(
        db,
        run,
        step_type="matter_stage",
        action_key=initial_stage["stage_key"],
        status="succeeded",
        evidence={"before": run.prior_stage, "after": initial_stage["label"]},
    )

    for item in current_preview["tasks"]:
        assigned_to_user_id: uuid.UUID | None = None
        preview_assignee = item["assigned_to_user_id"]
        if preview_assignee == "approval_actor":
            assigned_to_user_id = actor_user_id
        elif preview_assignee:
            assigned_to_user_id = uuid.UUID(preview_assignee)
        if assigned_to_user_id and not await _active_same_tenant_user(
            db, run.tenant_id, assigned_to_user_id
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Assignee for {item['item_key']} is no longer active in this firm",
            )
        task = Task(
            tenant_id=run.tenant_id,
            title=item["title"],
            description=item["description"],
            task_type=item["task_type"],
            status="pending",
            priority=item["priority"],
            due_date=date.fromisoformat(item["due_date"]),
            matter_id=run.matter_id,
            assigned_to_user_id=assigned_to_user_id,
            created_by_user_id=actor_user_id,
            source="workflow",
            external_ref=f"workflow:{run.id}:{item['item_key']}",
        )
        db.add(task)
        await db.flush()
        append_task_event(
            db,
            task,
            event_type="workflow_task_created",
            actor_user_id=actor_user_id,
            to_status="pending",
            metadata={
                "workflow_run_id": str(run.id),
                "workflow_item_key": item["item_key"],
                "stage_key": item["stage_key"],
            },
        )
        await append_run_step(
            db,
            run,
            step_type="task_create",
            action_key=item["item_key"],
            status="succeeded",
            task_id=task.id,
            evidence={
                "title": task.title,
                "description": task.description,
                "task_type": task.task_type,
                "priority": task.priority,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "assigned_to_user_id": (
                    str(task.assigned_to_user_id) if task.assigned_to_user_id else None
                ),
                "external_ref": task.external_ref,
                "initial_version": task.version,
            },
        )
    await append_run_event(
        db,
        run,
        event_type="applied",
        actor_user_id=actor_user_id,
        detail={
            "preview_sha256": run.preview_sha256,
            "task_count": len(current_preview["tasks"]),
        },
    )
    return run


async def rollback_run(
    db: AsyncSession,
    *,
    run: MatterWorkflowRun,
    matter: Matter,
    actor_user_id: uuid.UUID,
    idempotency_key: str,
    request_sha256: str,
    reason: str,
) -> tuple[MatterWorkflowRun, list[str]]:
    if run.rollback_idempotency_key:
        if (
            run.rollback_idempotency_key != idempotency_key
            or run.rollback_request_sha256 != request_sha256
        ):
            raise HTTPException(
                status_code=409,
                detail="Rollback Idempotency-Key was already used with different input",
            )
        if run.status == "rolled_back":
            return run, []
        if run.status == "compensation_required":
            immutable_detail = await db.scalar(
                select(MatterWorkflowRunEvent.detail_json)
                .where(
                    MatterWorkflowRunEvent.tenant_id == run.tenant_id,
                    MatterWorkflowRunEvent.run_id == run.id,
                    MatterWorkflowRunEvent.event_type == "rollback_blocked",
                )
                .order_by(MatterWorkflowRunEvent.sequence.desc())
                .limit(1)
            )
            if isinstance(immutable_detail, dict) and isinstance(
                immutable_detail.get("blockers"), list
            ):
                return run, list(immutable_detail["blockers"])
            return run, [
                run.failure_detail or "rollback requires manual compensation review"
            ]
    if run.status == "compensation_required":
        raise HTTPException(
            status_code=409,
            detail="Workflow rollback requires explicit manual compensation",
        )
    if run.status != "applied":
        raise HTTPException(status_code=409, detail=f"Workflow run is {run.status}")
    run.rollback_idempotency_key = idempotency_key
    run.rollback_request_sha256 = request_sha256
    await append_run_event(
        db,
        run,
        event_type="rollback_requested",
        actor_user_id=actor_user_id,
        detail={"reason": reason},
    )

    created_steps = (
        (
            await db.execute(
                select(MatterWorkflowRunStep)
                .where(
                    MatterWorkflowRunStep.tenant_id == run.tenant_id,
                    MatterWorkflowRunStep.run_id == run.id,
                    MatterWorkflowRunStep.step_type == "task_create",
                )
                .order_by(MatterWorkflowRunStep.sequence)
            )
        )
        .scalars()
        .all()
    )
    task_ids = [step.task_id for step in created_steps if step.task_id]
    tasks = (
        (
            await db.execute(
                select(Task)
                .where(Task.tenant_id == run.tenant_id, Task.id.in_(task_ids))
                .order_by(Task.created_at)
                .with_for_update()
            )
        )
        .scalars()
        .all()
        if task_ids
        else []
    )
    tasks_by_id = {task.id: task for task in tasks}
    blockers: list[str] = []
    for step in created_steps:
        task = tasks_by_id.get(step.task_id)
        evidence = step.evidence_json
        if task is None:
            blockers.append(f"task {step.task_id} is missing")
            continue
        unchanged = (
            task.status == "pending"
            and task.version == evidence.get("initial_version", 1)
            and task.title == evidence.get("title")
            and task.description == evidence.get("description")
            and task.task_type == evidence.get("task_type")
            and task.priority == evidence.get("priority")
            and (task.due_date.isoformat() if task.due_date else None)
            == evidence.get("due_date")
            and (str(task.assigned_to_user_id) if task.assigned_to_user_id else None)
            == evidence.get("assigned_to_user_id")
            and task.external_ref == evidence.get("external_ref")
        )
        if not unchanged:
            blockers.append(f"task {task.id} changed after apply")
    expected_stage = run.preview_json["initial_stage"]["label"]
    if matter.stage != expected_stage:
        blockers.append("matter stage changed after apply")

    if blockers:
        run.status = "compensation_required"
        run.failure_code = "rollback_blocked"
        run.failure_detail = "; ".join(blockers)[:2_000]
        await append_run_event(
            db,
            run,
            event_type="rollback_blocked",
            actor_user_id=actor_user_id,
            detail={"blockers": blockers},
        )
        for index, blocker in enumerate(blockers):
            await append_run_step(
                db,
                run,
                step_type="task_cancel"
                if blocker.startswith("task ")
                else "stage_restore",
                action_key=f"blocked_{index + 1}",
                status="blocked",
                evidence={"reason": blocker},
            )
        return run, blockers

    for step in created_steps:
        task = tasks_by_id[step.task_id]
        transition_task(
            db,
            task,
            to_status="cancelled",
            actor_user_id=actor_user_id,
            expected_version=task.version,
            reason=f"Workflow rollback: {reason}",
        )
        await append_run_step(
            db,
            run,
            step_type="task_cancel",
            action_key=step.action_key,
            status="succeeded",
            task_id=task.id,
            evidence={"from_status": "pending", "to_status": "cancelled"},
        )
    matter.stage = run.prior_stage
    await append_run_step(
        db,
        run,
        step_type="stage_restore",
        action_key="restore_prior_stage",
        status="succeeded",
        evidence={"from": expected_stage, "to": run.prior_stage},
    )
    run.status = "rolled_back"
    run.rolled_back_by_user_id = actor_user_id
    run.rolled_back_at = datetime.now(timezone.utc)
    run.failure_code = None
    run.failure_detail = None
    await append_run_event(
        db,
        run,
        event_type="rolled_back",
        actor_user_id=actor_user_id,
        detail={"reason": reason, "cancelled_task_count": len(tasks)},
    )
    return run, []


async def run_response(db: AsyncSession, run: MatterWorkflowRun) -> dict[str, Any]:
    events = (
        (
            await db.execute(
                select(MatterWorkflowRunEvent)
                .where(
                    MatterWorkflowRunEvent.tenant_id == run.tenant_id,
                    MatterWorkflowRunEvent.run_id == run.id,
                )
                .order_by(MatterWorkflowRunEvent.sequence)
            )
        )
        .scalars()
        .all()
    )
    steps = (
        (
            await db.execute(
                select(MatterWorkflowRunStep)
                .where(
                    MatterWorkflowRunStep.tenant_id == run.tenant_id,
                    MatterWorkflowRunStep.run_id == run.id,
                )
                .order_by(MatterWorkflowRunStep.sequence)
            )
        )
        .scalars()
        .all()
    )
    return {
        "id": run.id,
        "matter_id": run.matter_id,
        "template_version_id": run.template_version_id,
        "status": run.status,
        "preview_sha256": run.preview_sha256,
        "preview": run.preview_json,
        "approved_by_user_id": run.approved_by_user_id,
        "approved_at": run.approved_at,
        "rolled_back_by_user_id": run.rolled_back_by_user_id,
        "rolled_back_at": run.rolled_back_at,
        "failure_code": run.failure_code,
        "failure_detail": run.failure_detail,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "events": [
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "actor_user_id": event.actor_user_id,
                "detail": event.detail_json,
                "evidence_sha256": event.evidence_sha256,
                "created_at": event.created_at,
            }
            for event in events
        ],
        "steps": [
            {
                "sequence": step.sequence,
                "step_type": step.step_type,
                "action_key": step.action_key,
                "status": step.status,
                "task_id": step.task_id,
                "evidence": step.evidence_json,
                "evidence_sha256": step.evidence_sha256,
                "created_at": step.created_at,
            }
            for step in steps
        ],
    }
