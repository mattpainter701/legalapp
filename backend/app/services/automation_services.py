"""Control-plane operations for human-approved unattended preparation rules."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.automation_service import AutomationServiceIdentity, AutomationServiceRule
from app.models.user import User
from app.models.workflow_run import WorkflowRun
from app.services.automation_capabilities import CapabilityError
from app.services.automation_service_contract import ServiceIdentityInput, ServiceRuleInput
from app.services.automation_service_plan import freeze_service_plan
from app.services.configurable_workflows import digest_payload
from app.services.rbac_service import get_user_capabilities
from app.services.workflow_run_payloads import seal_payload
from app.services.workflow_run_contract import plan_metadata


def _definition(identity, rule, metadata):
    return digest_payload({
        "identity_id": str(identity.id), "identity_version": identity.version,
        "matter_id": str(rule.matter_id), "source_run_id": str(rule.source_run_id),
        "schedule": rule.schedule, "plan_sha256": digest_payload(metadata),
        "event_rule_id": str(rule.event_rule_id) if rule.event_rule_id else None,
        "event_rule_sha256": rule.event_rule_sha256,
    })


async def create_identity(db, *, tenant_id, actor, body: ServiceIdentityInput):
    """Create the only kind of service actor accepted by the DB guards."""
    principal_id = uuid.uuid4()
    service_user = User(
        id=principal_id,
        tenant_id=tenant_id,
        # This cannot receive mail or authenticate; its unique address gives
        # evidence a durable FK without creating a real account.
        email=f"automation-service-{principal_id}@invalid.getlawhand.com",
        full_name=body.name,
        role="user",
        principal_type="automation_service",
        is_active=False,
        license_active=False,
        workspace_mcp_enabled=False,
        premium_ai_enabled=False,
    )
    identity = AutomationServiceIdentity(
        id=uuid.uuid4(), tenant_id=tenant_id, user_id=principal_id,
        name=body.name, capabilities=sorted(set(body.capabilities)),
        created_by_user_id=actor.id, status="active", version=1,
    )
    db.add_all((service_user, identity))
    await db.flush()
    return identity


async def create_rule(db, *, tenant_id, actor, body: ServiceRuleInput):
    """Freeze one completed ledger; no arbitrary schedule DSL or plan JSON."""
    identity = await db.scalar(select(AutomationServiceIdentity).where(
        AutomationServiceIdentity.tenant_id == tenant_id,
        AutomationServiceIdentity.id == body.identity_id,
    ).with_for_update())
    run = await db.scalar(select(WorkflowRun).where(
        WorkflowRun.tenant_id == tenant_id, WorkflowRun.id == body.source_run_id,
    ).with_for_update())
    if not identity or not run:
        raise CapabilityError("service_source_unavailable", "Choose a current service identity and completed workflow")
    if identity.status != "active":
        raise CapabilityError("service_identity_disabled", "Enable the named service before preparing a rule")
    plan, sources = await freeze_service_plan(db, run, identity.capabilities)
    rule_id = uuid.uuid4()
    metadata = plan_metadata(plan)
    payload = {"plan": plan.model_dump(mode="json"), "sources": sources}
    ciphertext, payload_sha256 = seal_payload(
        payload, tenant_id=tenant_id, run_id=rule_id, step_id=rule_id, kind="service_plan"
    )
    event_rule_id = body.schedule.event_rule_id
    event_rule_sha256 = None
    if event_rule_id:
        from app.models.workflow_automation import MatterWorkflowAutomationRule
        event_rule = await db.scalar(select(MatterWorkflowAutomationRule).where(
            MatterWorkflowAutomationRule.tenant_id == tenant_id,
            MatterWorkflowAutomationRule.id == event_rule_id,
            MatterWorkflowAutomationRule.status == "active",
        ))
        if not event_rule or event_rule.template_id is None:
            raise CapabilityError("service_event_rule_unavailable", "Choose an active lifecycle rule")
        event_rule_sha256 = event_rule.definition_sha256
    rule = AutomationServiceRule(
        id=rule_id, tenant_id=tenant_id, name=body.name.strip(), identity_id=identity.id,
        matter_id=run.matter_id, source_run_id=run.id,
        schedule=body.schedule.model_dump(mode="json"), plan_metadata=metadata,
        plan_ciphertext=ciphertext, payload_sha256=payload_sha256,
        plan_sha256=digest_payload(metadata), definition_sha256="0" * 64,
        event_rule_id=event_rule_id, event_rule_sha256=event_rule_sha256,
        created_by_user_id=actor.id, status="draft", version=1,
    )
    rule.definition_sha256 = _definition(identity, rule, metadata)
    db.add(rule)
    await db.flush()
    return rule


async def approve_rule(db, *, tenant_id, actor, rule_id, expected_version):
    """Approval is explicit and rechecks the existing legal-work capability."""
    caps = await get_user_capabilities(db, actor.id)
    if "approve_legal_work" not in caps:
        raise CapabilityError("approval_permission_denied", "Legal-work approval is required")
    rule = await db.scalar(select(AutomationServiceRule).where(
        AutomationServiceRule.tenant_id == tenant_id, AutomationServiceRule.id == rule_id,
    ).with_for_update())
    if not rule:
        raise CapabilityError("service_rule_not_found", "Service rule not found")
    if rule.version != expected_version:
        raise CapabilityError("service_rule_version_conflict", "Reload the service rule before approving it")
    if rule.status != "draft":
        raise CapabilityError("service_rule_not_draft", "Only a reviewed draft can be approved")
    if not actor.is_active or not actor.license_active:
        raise CapabilityError("approval_actor_unavailable", "The approving attorney is unavailable")
    rule.status = "active"
    rule.approved_by_user_id = actor.id
    rule.approved_at = datetime.now(timezone.utc)
    rule.version += 1
    rule.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return rule


async def set_rule_status(db, *, tenant_id, actor, rule_id, expected_version, status):
    if status not in {"active", "paused"}:
        raise CapabilityError("invalid_service_rule_status", "A rule may only be active or paused")
    rule = await db.scalar(select(AutomationServiceRule).where(
        AutomationServiceRule.tenant_id == tenant_id, AutomationServiceRule.id == rule_id,
    ).with_for_update())
    if not rule or rule.version != expected_version:
        raise CapabilityError("service_rule_version_conflict", "Reload the service rule before changing it")
    if rule.status == "draft":
        raise CapabilityError("service_rule_not_approved", "Approve the rule before changing its schedule state")
    rule.status, rule.version, rule.updated_at = status, rule.version + 1, datetime.now(timezone.utc)
    await db.flush()
    return rule
