"""Transactional matter-trigger outbox. Jobs only prepare work for human review.

Reentry is deliberately once per rule/matter/condition, including blocked jobs.
Changed facts require a fresh manual preview, not replay against today's facts.
Payloads contain identifiers and fingerprints, never matter/document content.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.durable_job import DurableJob
from app.models.plugin import Matter
from app.models.user import User
from app.models.workflow_automation import MatterWorkflowAutomationRule
from app.services import workflow_automations as planning
from app.services.configurable_workflows import matter_snapshot, digest_payload
from app.services.durable_jobs import enqueue_job
from app.services.rbac_service import get_user_capabilities

JOB_KIND = "matter_workflow_plan"
BLOCK_REASONS = {
    "source_unavailable": "The original matter or rule is unavailable.",
    "actor_unavailable": "The original user's account or license is unavailable.",
    "actor_permission_changed": "The original user no longer has matter-management permission.",
    "rule_changed": "The rule changed or was archived after this trigger.",
    "matter_archived": "The matter was archived after this trigger.",
    "matter_changed": "Workflow facts changed after this trigger.",
    "template_changed": "The approved template changed or is no longer available.",
    "inactive_tenant": "The firm's account is inactive.",
}


def blocked_message(code):
    return f"{BLOCK_REASONS.get(code, 'The original context is unavailable.')} Review current facts and create a manual preview."


async def enqueue_matter_event(db, *, matter, trigger_event, actor_user_id):
    """Flush/enqueue in the caller's transaction; failure must roll back the save."""
    if trigger_event not in planning.TRIGGER_EVENTS:
        raise ValueError("Unsupported workflow trigger")
    await db.flush()
    await planning.acquire_workflow_config_lock(db, matter.tenant_id, shared=True)
    rules = await planning.active_rules_for(
        db, matter.tenant_id, trigger_event=trigger_event
    )
    matched = [
        r
        for r in rules
        if planning.rule_matches(r, matter, trigger_event=trigger_event)
    ]
    if not matched:
        return []
    snapshot, _ = await matter_snapshot(db, matter, lock_definitions=True)
    jobs = []
    for rule in matched:
        version = await planning.latest_approved_version_id(
            db, matter.tenant_id, rule.template_id
        )
        key = planning.dedupe_key(rule, matter, trigger_event=trigger_event)
        jobs.append(
            await enqueue_job(
                db,
                tenant_id=matter.tenant_id,
                kind=JOB_KIND,
                idempotency_key=f"{rule.id}:{key}",
                payload={
                    "matter_id": str(matter.id),
                    "rule_id": str(rule.id),
                    "actor_user_id": str(actor_user_id),
                    "trigger_event": trigger_event,
                    "rule_sha256": rule.definition_sha256,
                    "matter_sha256": digest_payload(snapshot),
                    "template_version_id": str(version) if version else None,
                    "as_of": date.today().isoformat(),
                    "dedupe_key": key,
                },
            )
        )
    return jobs


async def run_planning_job(db: AsyncSession, job: DurableJob) -> dict:
    """Caller holds job lock and commits plan + job completion atomically.

    No exception swallowing: SQL/infrastructure errors roll back and retry. A crash
    after commit redelivers the same job/event keys without another run or task.
    """
    p = job.payload
    await planning.acquire_workflow_config_lock(db, job.tenant_id, shared=True)
    rule = await db.scalar(
        select(MatterWorkflowAutomationRule)
        .where(
            MatterWorkflowAutomationRule.id == uuid.UUID(p["rule_id"]),
            MatterWorkflowAutomationRule.tenant_id == job.tenant_id,
        )
        .with_for_update()
    )
    matter = await db.scalar(
        select(Matter)
        .where(
            Matter.id == uuid.UUID(p["matter_id"]),
            Matter.tenant_id == job.tenant_id,
        )
        .with_for_update()
    )
    if rule is None or matter is None:
        return {"outcome": "blocked", "failure_code": "source_unavailable"}
    existing = await planning._existing_dispatch(db, rule, p["dedupe_key"])
    if existing:
        return {"outcome": existing.outcome, "event_id": str(existing.id)}
    actor_id = uuid.UUID(p["actor_user_id"])
    actor = await db.scalar(
        select(User)
        .where(
            User.id == actor_id,
            User.tenant_id == job.tenant_id,
        )
        .with_for_update(read=True)
    )
    reason = None
    if actor is None:
        return {"outcome": "blocked", "failure_code": "actor_unavailable"}
    if not actor.is_active or not actor.license_active:
        reason = "actor_unavailable"
    elif "manage_matters" not in await get_user_capabilities(db, actor_id):
        reason = "actor_permission_changed"
    elif rule.status != "active" or rule.definition_sha256 != p["rule_sha256"]:
        reason = "rule_changed"
    elif matter.archived_at:
        reason = "matter_archived"
    else:
        snapshot, _ = await matter_snapshot(db, matter, lock_definitions=True)
        version = await planning.latest_approved_version_id(
            db, job.tenant_id, rule.template_id
        )
        if digest_payload(snapshot) != p["matter_sha256"]:
            reason = "matter_changed"
        elif (str(version) if version else None) != p[
            "template_version_id"
        ] or version is None:
            reason = "template_changed"
    if reason:
        event = planning._record_dispatch(
            db,
            rule,
            matter=matter,
            trigger_event=p["trigger_event"],
            key=p["dedupe_key"],
            outcome="blocked",
            run_id=None,
            actor_user_id=actor_id,
            trigger_rule_sha256=p["rule_sha256"],
            detail={
                "failure_code": reason,
                "message": blocked_message(reason),
                "trigger_rule_sha256": p["rule_sha256"],
                "trigger_matter_sha256": p["matter_sha256"],
            },
        )
    else:
        event = await planning._plan_for_rule(
            db,
            rule,
            matter=matter,
            trigger_event=p["trigger_event"],
            actor_user_id=actor_id,
            as_of=date.fromisoformat(p["as_of"]),
        )
    await db.flush()
    return {"outcome": event.outcome, "event_id": str(event.id)}


async def pending_activity(db, tenant_id, *, matter_id=None, rule_id=None, limit=50):
    """Reuse activity endpoints; completed plans have immutable event receipts."""
    query = select(DurableJob).where(
        DurableJob.tenant_id == tenant_id, DurableJob.kind == JOB_KIND
    )
    if matter_id:
        query = query.where(
            DurableJob.payload["matter_id"].as_string() == str(matter_id)
        )
    if rule_id:
        query = query.where(DurableJob.payload["rule_id"].as_string() == str(rule_id))
    rows = (
        await db.scalars(query.order_by(DurableJob.created_at.desc()).limit(limit))
    ).all()
    return [
        {
            "id": str(row.id),
            "rule_id": row.payload["rule_id"],
            "matter_id": row.payload["matter_id"],
            "trigger_event": row.payload["trigger_event"],
            "outcome": (row.result or {}).get(
                "outcome",
                "retrying" if row.status == "pending" and row.attempts else row.status,
            ),
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "detail": {
                "message": (
                    "Planning failed. Review the matter and create a manual preview."
                    if row.status == "failed"
                    else blocked_message((row.result or {}).get("failure_code"))
                    if row.status == "completed"
                    else "Planning is queued; no work has been applied."
                ),
                "failure_code": (row.result or {}).get("failure_code"),
            },
            "created_at": row.created_at,
        }
        for row in rows
        if row.status != "completed" or not (row.result or {}).get("event_id")
    ]
