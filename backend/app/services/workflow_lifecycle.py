"""Prepare bounded workflow runs from transactional lifecycle occurrences.

The database capture function freezes matching rule/version identities and
source/matter fingerprints in the source-write transaction. This consumer only
plans work for human review; no task application or provider call occurs here.
"""

import uuid
from datetime import date

from sqlalchemy import select, text

from app.models.plugin import Matter
from app.models.user import User
from app.models.workflow_automation import MatterWorkflowAutomationRule
from app.services import workflow_automations as planning
from app.services.rbac_service import get_user_capabilities


JOB_KIND = "workflow_lifecycle_plan"
LIFECYCLE_EVENTS = (
    "task_completed",
    "document_received",
    "intake_submitted",
    "esign_completed",
    "deadline_approaching",
    "invoice_overdue",
    "inbound_email_matched_to_matter",
    "payment_received",
)
SOURCE_KINDS = frozenset(
    {
        "task",
        "document",
        "matter_intake",
        "intake_submission",
        "signature",
        "invoice",
        "inbound_email",
        "payment",
    }
)


async def run_lifecycle_job(db, job):
    payload = job.payload or {}
    if (
        payload.get("trigger_event") not in LIFECYCLE_EVENTS
        or payload.get("source_kind") not in SOURCE_KINDS
    ):
        raise ValueError("Invalid lifecycle planning payload")
    rule_id, matter_id, actor_id, source_id = (
        uuid.UUID(payload[key])
        for key in ("rule_id", "matter_id", "actor_user_id", "source_id")
    )
    await planning.acquire_workflow_config_lock(db, job.tenant_id, shared=True)
    rule = await db.scalar(
        select(MatterWorkflowAutomationRule)
        .where(
            MatterWorkflowAutomationRule.tenant_id == job.tenant_id,
            MatterWorkflowAutomationRule.id == rule_id,
        )
        .with_for_update()
    )
    matter = await db.scalar(
        select(Matter)
        .where(
            Matter.tenant_id == job.tenant_id,
            Matter.id == matter_id,
        )
        .with_for_update(of=Matter)
    )
    if rule is None or matter is None:
        return {"outcome": "blocked", "failure_code": "source_unavailable"}
    existing = await planning._existing_dispatch(db, rule, payload["dedupe_key"])
    if existing is not None:
        return {"outcome": existing.outcome, "event_id": str(existing.id)}
    actor = await db.scalar(
        select(User)
        .where(
            User.tenant_id == job.tenant_id,
            User.id == actor_id,
        )
        .with_for_update(read=True)
    )
    if actor is None:
        return {"outcome": "blocked", "failure_code": "actor_unavailable"}
    reason = None
    if not actor.is_active or not actor.license_active:
        reason = "actor_unavailable"
    elif "manage_matters" not in await get_user_capabilities(db, actor_id):
        reason = "actor_permission_changed"
    elif (
        rule.status != "active"
        or rule.definition_sha256 != payload["rule_sha256"]
        or rule.activated_by_user_id != actor_id
    ):
        reason = "rule_changed"
    elif matter.archived_at:
        reason = "matter_archived"
    else:
        current = await db.scalar(
            text(
                "SELECT public.workflow_lifecycle_fingerprint(:tenant, :matter, :source_kind, :source, :trigger)"
            ),
            {
                "tenant": job.tenant_id,
                "matter": matter_id,
                "source_kind": payload["source_kind"],
                "source": source_id,
                "trigger": payload["trigger_event"],
            },
        )
        version = await planning.latest_approved_version_id(
            db, job.tenant_id, rule.template_id
        )
        if current != payload["context_sha256"]:
            reason = "source_changed"
        elif version is None or str(version) != payload["template_version_id"]:
            reason = "template_changed"
    if reason:
        event = planning._record_dispatch(
            db,
            rule,
            matter=matter,
            trigger_event=payload["trigger_event"],
            key=payload["dedupe_key"],
            outcome="blocked",
            run_id=None,
            actor_user_id=actor_id,
            trigger_rule_sha256=payload["rule_sha256"],
            detail={
                "failure_code": reason,
                "source_id": str(source_id),
                "source_kind": payload["source_kind"],
                "context_sha256": payload["context_sha256"],
                "message": "The original trigger context is unavailable or changed. Review current facts and prepare a manual preview.",
            },
        )
    else:
        event = await planning._plan_for_rule(
            db,
            rule,
            matter=matter,
            trigger_event=payload["trigger_event"],
            actor_user_id=actor_id,
            as_of=date.fromisoformat(payload["as_of"]),
            dispatch_key=payload["dedupe_key"],
            trigger_evidence={
                "source_id": str(source_id),
                "source_kind": payload["source_kind"],
                "context_sha256": payload["context_sha256"],
            },
        )
    await db.flush()
    return {"outcome": event.outcome, "event_id": str(event.id)}


async def enqueue_due_events_for_tenant(db, tenant_id):
    """A scheduler transaction captures each deadline/invoice condition once.

    Duplicate scans reuse the occurrence key, including blocked occurrences.
    Changing a due date creates a different occurrence. The capture function
    itself selects only active, matching rules and freezes their approved version.
    """
    await db.execute(
        text("""
        SELECT public.capture_workflow_lifecycle_event(
          :tenant, t.matter_id, 'deadline_approaching', 'task', t.id,
          t.due_date::text)
        FROM tasks t
        WHERE t.tenant_id=:tenant AND t.task_type='deadline'
          AND t.status NOT IN ('completed','cancelled') AND t.matter_id IS NOT NULL
          AND t.due_date BETWEEN current_date AND current_date + 3
          AND EXISTS (SELECT 1 FROM matter_workflow_automation_rules r
            WHERE r.tenant_id=:tenant AND r.status='active' AND r.trigger_event='deadline_approaching')
    """),
        {"tenant": tenant_id},
    )
    await db.execute(
        text("""
        SELECT public.capture_workflow_lifecycle_event(
          :tenant, i.matter_id, 'invoice_overdue', 'invoice', i.id,
          i.due_date::text)
        FROM invoices i
        WHERE i.tenant_id=:tenant AND i.status IN ('sent','overdue','partially_paid')
          AND i.due_date < current_date
          AND i.total > coalesce((SELECT sum(p.amount) FROM payments p
            WHERE p.tenant_id=i.tenant_id AND p.invoice_id=i.id),0)
          AND EXISTS (SELECT 1 FROM matter_workflow_automation_rules r
            WHERE r.tenant_id=:tenant AND r.status='active' AND r.trigger_event='invoice_overdue')
    """),
        {"tenant": tenant_id},
    )
