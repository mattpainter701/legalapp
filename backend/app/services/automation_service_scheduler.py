"""Tenant-scoped scheduler admission for approved unattended service rules."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.automation_service import AutomationServiceIdentity, AutomationServiceOccurrence, AutomationServiceRule
from app.models.user import User
from app.models.workflow_automation import MatterWorkflowAutomationEvent
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.automation_service_contract import ServiceSchedule, due_occurrence
from app.services.workflow_run_contract import WorkflowRunInput
from app.services.workflow_run_ledger import submit_run
from app.services.workflow_run_payloads import open_payload


MAX_IDENTITY_DAILY_RUNS = 10
MAX_TENANT_DAILY_RUNS = 100


async def schedule_due_rules(db, tenant_id, *, now=None):
    """Start today's bounded occurrences once; never catch up an arbitrary backlog."""
    now = now or datetime.now(timezone.utc)
    rules = (await db.scalars(select(AutomationServiceRule).where(
        AutomationServiceRule.tenant_id == tenant_id, AutomationServiceRule.status == "active"
    ).with_for_update(skip_locked=True))).all()
    outcomes = []
    for rule in rules:
        schedule = ServiceSchedule.model_validate(rule.schedule)
        source_event = None
        key = due_occurrence(schedule, now, rule.approved_at)
        if schedule.kind == "workflow_event":
            source_event = await db.scalar(select(MatterWorkflowAutomationEvent).where(
                MatterWorkflowAutomationEvent.tenant_id == tenant_id,
                MatterWorkflowAutomationEvent.rule_id == rule.event_rule_id,
                MatterWorkflowAutomationEvent.matter_id == rule.matter_id,
                MatterWorkflowAutomationEvent.outcome == "planned",
                MatterWorkflowAutomationEvent.rule_sha256 == rule.event_rule_sha256,
                ~MatterWorkflowAutomationEvent.id.in_(select(AutomationServiceOccurrence.source_event_id).where(
                    AutomationServiceOccurrence.tenant_id == tenant_id,
                    AutomationServiceOccurrence.source_event_id.is_not(None),
                )),
            ).order_by(MatterWorkflowAutomationEvent.created_at).with_for_update(skip_locked=True))
            key = f"event:{source_event.id}" if source_event else None
        if not key:
            continue
        identity = await db.scalar(select(AutomationServiceIdentity).where(
            AutomationServiceIdentity.tenant_id == tenant_id, AutomationServiceIdentity.id == rule.identity_id,
            AutomationServiceIdentity.status == "active",
        ))
        if not identity:
            continue
        existing = await db.scalar(select(AutomationServiceOccurrence).where(
            AutomationServiceOccurrence.tenant_id == tenant_id, AutomationServiceOccurrence.rule_id == rule.id,
            AutomationServiceOccurrence.occurrence_key == key,
        ))
        if existing:
            outcomes.append(existing)
            continue
        today = now.date()
        identity_count = await db.scalar(select(func.count()).select_from(AutomationServiceOccurrence).where(
            AutomationServiceOccurrence.tenant_id == tenant_id, AutomationServiceOccurrence.identity_id == identity.id,
            AutomationServiceOccurrence.created_at >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
            AutomationServiceOccurrence.outcome == "started",
        ))
        if identity_count and identity_count >= MAX_IDENTITY_DAILY_RUNS:
            row = AutomationServiceOccurrence(tenant_id=tenant_id, rule_id=rule.id, identity_id=identity.id,
                occurrence_key=key, source_event_id=source_event.id if source_event else None,
                rule_sha256=rule.definition_sha256, outcome="blocked", failure_code="identity_daily_budget")
            db.add(row)
            outcomes.append(row)
            continue
        tenant_count = await db.scalar(select(func.count()).select_from(AutomationServiceOccurrence).where(
            AutomationServiceOccurrence.tenant_id == tenant_id,
            AutomationServiceOccurrence.created_at >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
            AutomationServiceOccurrence.outcome == "started",
        ))
        if tenant_count and tenant_count >= MAX_TENANT_DAILY_RUNS:
            row = AutomationServiceOccurrence(tenant_id=tenant_id, rule_id=rule.id, identity_id=identity.id,
                occurrence_key=key, source_event_id=source_event.id if source_event else None,
                rule_sha256=rule.definition_sha256, outcome="blocked", failure_code="tenant_daily_budget")
            db.add(row)
            outcomes.append(row)
            continue
        service_user = await db.scalar(select(User).where(User.tenant_id == tenant_id, User.id == identity.user_id))
        payload = open_payload(rule.plan_ciphertext, tenant_id=tenant_id, run_id=rule.id, step_id=rule.id,
                               kind="service_plan", expected_sha256=rule.payload_sha256)
        plan = WorkflowRunInput.model_validate(payload["plan"]).model_copy(update={"request_id": uuid.uuid4()})
        context = CapabilityContext(db=db, user=service_user, channel="automation_service",
            review_owner_user_id=rule.approved_by_user_id, service_rule_id=rule.id,
            service_rule_sha256=rule.definition_sha256, allowed_sources=payload.get("sources"))
        try:
            run = await submit_run(context, plan)
        except CapabilityError as error:
            row = AutomationServiceOccurrence(tenant_id=tenant_id, rule_id=rule.id, identity_id=identity.id,
                occurrence_key=key, source_event_id=source_event.id if source_event else None,
                rule_sha256=rule.definition_sha256, outcome="blocked", failure_code=error.code)
        else:
            row = AutomationServiceOccurrence(tenant_id=tenant_id, rule_id=rule.id, identity_id=identity.id,
                occurrence_key=key, source_event_id=source_event.id if source_event else None,
                rule_sha256=rule.definition_sha256, run_id=uuid.UUID(run["run_id"]), outcome="started")
        db.add(row)
        outcomes.append(row)
    await db.flush()
    return outcomes
