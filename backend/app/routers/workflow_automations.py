"""Firm configuration and evidence for bounded matter workflow automations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.models.configurable_workflow import MatterWorkflowTemplate
from app.models.plugin import Matter
from app.models.workflow_automation import (
    MatterWorkflowAutomationEvent,
    MatterWorkflowAutomationRule,
)
from app.schemas.workflow_automation import (
    WorkflowAutomationActivateRequest,
    WorkflowAutomationRuleInput,
)
from app.services.access_control import (
    require_any_capability,
    require_capability,
)
from app.services.configurable_workflows import acquire_workflow_config_lock
from app.services.durable_workflow_automations import pending_activity
from app.services.workflow_automations import (
    automation_event_response,
    count_rules,
    latest_approved_version_id,
    rule_definition_sha256,
    rule_response,
)

router = APIRouter(tags=["workflow-automations"])

# A firm reviews these rules by reading them. Keep the set small enough that
# reading all of them stays a realistic approval step.
MAX_RULES_PER_TENANT = 50


async def _rule_or_404(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    rule_id: uuid.UUID,
    *,
    lock: bool = False,
) -> MatterWorkflowAutomationRule:
    query = select(MatterWorkflowAutomationRule).where(
        MatterWorkflowAutomationRule.id == rule_id,
        MatterWorkflowAutomationRule.tenant_id == tenant_id,
    )
    if lock:
        query = query.with_for_update(of=MatterWorkflowAutomationRule)
    rule = await db.scalar(query)
    if rule is None:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    return rule


async def _template_or_404(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
) -> MatterWorkflowTemplate:
    template = await db.scalar(
        select(MatterWorkflowTemplate).where(
            MatterWorkflowTemplate.id == template_id,
            MatterWorkflowTemplate.tenant_id == tenant_id,
        )
    )
    if template is None or not template.active:
        raise HTTPException(status_code=404, detail="Workflow template not found")
    return template


async def _template_names(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    template_ids: set[uuid.UUID],
) -> dict[uuid.UUID, str]:
    if not template_ids:
        return {}
    rows = (
        await db.execute(
            select(MatterWorkflowTemplate.id, MatterWorkflowTemplate.name).where(
                MatterWorkflowTemplate.tenant_id == tenant_id,
                MatterWorkflowTemplate.id.in_(sorted(template_ids, key=str)),
            )
        )
    ).all()
    return {template_id: name for template_id, name in rows}


def _conflict(error: IntegrityError) -> HTTPException:
    message = str(getattr(error, "orig", error))
    if "uq_matter_workflow_automation_rules_name" in message:
        return HTTPException(
            status_code=409,
            detail="Another automation rule already uses that name",
        )
    if "uq_matter_workflow_automation_rules_active_trigger" in message:
        return HTTPException(
            status_code=409,
            detail="An active rule already plans that template for this trigger",
        )
    return HTTPException(status_code=409, detail="Automation rule conflict")


@router.get("/api/workflow-config/automations")
async def list_automation_rules(
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_any_capability("manage_workflows", "approve_legal_work")),
):
    await set_tenant_context(db, str(user.tenant_id))
    filters = [MatterWorkflowAutomationRule.tenant_id == user.tenant_id]
    if not include_archived:
        filters.append(MatterWorkflowAutomationRule.status != "archived")
    rules = list(
        (
            await db.execute(
                select(MatterWorkflowAutomationRule)
                .where(*filters)
                .order_by(
                    MatterWorkflowAutomationRule.created_at,
                    MatterWorkflowAutomationRule.id,
                )
            )
        ).scalars()
    )
    names = await _template_names(
        db, user.tenant_id, {rule.template_id for rule in rules}
    )
    return {
        "items": [
            rule_response(rule, template_name=names.get(rule.template_id))
            for rule in rules
        ]
    }


@router.post("/api/workflow-config/automations", status_code=201)
async def create_automation_rule(
    body: WorkflowAutomationRuleInput,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    await set_tenant_context(db, str(user.tenant_id))
    await acquire_workflow_config_lock(db, user.tenant_id, shared=False)
    template = await _template_or_404(db, user.tenant_id, body.template_id)
    if await count_rules(db, user.tenant_id) >= MAX_RULES_PER_TENANT:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A firm may keep at most {MAX_RULES_PER_TENANT} automation rules. "
                "Archive one before adding another."
            ),
        )
    rule = MatterWorkflowAutomationRule(
        tenant_id=user.tenant_id,
        name=body.name,
        trigger_event=body.trigger_event,
        trigger_stage=body.trigger_stage,
        match_matter_type=body.match_matter_type,
        match_practice_area=body.match_practice_area,
        template_id=body.template_id,
        status="draft",
        created_by_user_id=user.id,
    )
    rule.definition_sha256 = rule_definition_sha256(rule)
    db.add(rule)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise _conflict(error) from error
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    await db.refresh(rule)
    return rule_response(rule, template_name=template.name)


@router.patch("/api/workflow-config/automations/{rule_id}")
async def update_automation_rule(
    rule_id: uuid.UUID,
    body: WorkflowAutomationRuleInput,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    """Replace a rule's definition. A changed definition returns it to draft."""
    await set_tenant_context(db, str(user.tenant_id))
    await acquire_workflow_config_lock(db, user.tenant_id, shared=False)
    rule = await _rule_or_404(db, user.tenant_id, rule_id, lock=True)
    if rule.status == "archived":
        raise HTTPException(
            status_code=409, detail="An archived automation rule cannot be edited"
        )
    template = await _template_or_404(db, user.tenant_id, body.template_id)
    approved_definition = rule.definition_sha256
    rule.name = body.name
    rule.trigger_event = body.trigger_event
    rule.trigger_stage = body.trigger_stage
    rule.match_matter_type = body.match_matter_type
    rule.match_practice_area = body.match_practice_area
    rule.template_id = body.template_id
    rule.definition_sha256 = rule_definition_sha256(rule)
    if rule.definition_sha256 != approved_definition:
        # What the rule does changed, so the approval it carried no longer
        # covers it. The name is a label and does not cost an approval.
        rule.status = "draft"
        rule.activated_by_user_id = None
        rule.activated_at = None
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise _conflict(error) from error
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    await db.refresh(rule)
    return rule_response(rule, template_name=template.name)


@router.post("/api/workflow-config/automations/{rule_id}/activate")
async def activate_automation_rule(
    rule_id: uuid.UUID,
    body: WorkflowAutomationActivateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("approve_legal_work")),
):
    """Approve the exact reviewed definition and let the rule start firing."""
    await set_tenant_context(db, str(user.tenant_id))
    await acquire_workflow_config_lock(db, user.tenant_id, shared=False)
    rule = await _rule_or_404(db, user.tenant_id, rule_id, lock=True)
    if rule.status != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"Only a draft automation rule can be activated (status: {rule.status})",
        )
    current = rule_definition_sha256(rule)
    if current != rule.definition_sha256 or current != body.definition_sha256:
        raise HTTPException(
            status_code=409,
            detail="The automation rule changed since it was reviewed",
        )
    version_id = await latest_approved_version_id(db, user.tenant_id, rule.template_id)
    if version_id is None:
        raise HTTPException(
            status_code=409,
            detail="The rule's workflow template has no active, approved version",
        )
    rule.status = "active"
    rule.activated_by_user_id = user.id
    rule.activated_at = datetime.now(timezone.utc)
    try:
        await db.flush()
    except IntegrityError as error:
        await db.rollback()
        raise _conflict(error) from error
    await db.commit()
    await set_tenant_context(db, str(user.tenant_id))
    await db.refresh(rule)
    return rule_response(rule)


@router.post("/api/workflow-config/automations/{rule_id}/archive")
async def archive_automation_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_workflows")),
):
    """Retire a rule. Dispatch evidence and planned runs are kept."""
    await set_tenant_context(db, str(user.tenant_id))
    await acquire_workflow_config_lock(db, user.tenant_id, shared=False)
    rule = await _rule_or_404(db, user.tenant_id, rule_id, lock=True)
    if rule.status != "archived":
        rule.status = "archived"
        rule.activated_by_user_id = None
        rule.activated_at = None
        rule.archived_at = datetime.now(timezone.utc)
        await db.commit()
        await set_tenant_context(db, str(user.tenant_id))
        await db.refresh(rule)
    return rule_response(rule)


@router.get("/api/workflow-config/automations/{rule_id}/events")
async def list_automation_rule_events(
    rule_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_any_capability("manage_workflows", "approve_legal_work")),
):
    await set_tenant_context(db, str(user.tenant_id))
    rule = await _rule_or_404(db, user.tenant_id, rule_id)
    events = (
        (
            await db.execute(
                select(MatterWorkflowAutomationEvent)
                .where(
                    MatterWorkflowAutomationEvent.tenant_id == user.tenant_id,
                    MatterWorkflowAutomationEvent.rule_id == rule.id,
                )
                .order_by(MatterWorkflowAutomationEvent.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": await pending_activity(
            db, user.tenant_id, rule_id=rule_id, limit=limit
        )
        + [automation_event_response(event) for event in events]
    }


@router.get("/api/matters/{matter_id}/workflow-automation-events")
async def list_matter_automation_events(
    matter_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_capability("manage_matters")),
):
    await set_tenant_context(db, str(user.tenant_id))
    matter = await db.scalar(
        select(Matter).where(Matter.id == matter_id, Matter.tenant_id == user.tenant_id)
    )
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    events = (
        (
            await db.execute(
                select(MatterWorkflowAutomationEvent)
                .where(
                    MatterWorkflowAutomationEvent.tenant_id == user.tenant_id,
                    MatterWorkflowAutomationEvent.matter_id == matter_id,
                )
                .order_by(MatterWorkflowAutomationEvent.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    rule_ids = {event.rule_id for event in events}
    names: dict[uuid.UUID, str] = {}
    if rule_ids:
        rows = (
            await db.execute(
                select(
                    MatterWorkflowAutomationRule.id,
                    MatterWorkflowAutomationRule.name,
                ).where(
                    MatterWorkflowAutomationRule.tenant_id == user.tenant_id,
                    MatterWorkflowAutomationRule.id.in_(sorted(rule_ids, key=str)),
                )
            )
        ).all()
        names = {rule_id: name for rule_id, name in rows}
    return {
        "items": await pending_activity(
            db, user.tenant_id, matter_id=matter_id, limit=limit
        )
        + [
            {
                **automation_event_response(event),
                "rule_name": names.get(event.rule_id),
            }
            for event in events
        ]
    }
