"""Shared run submission, retrieval and bounded human-input continuation."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text, func

from app.models.workflow_run import WorkflowRun, WorkflowRunStep, WorkflowRunEvent
from app.services.automation_capabilities import (
    CapabilityError,
    resolve_capability_spec,
)
from app.services.configurable_workflows import digest_payload
from app.services.durable_jobs import enqueue_job
from app.services.workflow_run_contract import plan_metadata
from app.services.workflow_run_payloads import seal_payload, open_payload
from app.services.workflow_run_authority import current_context, verify_source_bindings


async def append_event(db, run, event_type, *, step=None, actor_id=None, metadata=None):
    # Caller holds the run row lock (or owns its uncommitted insertion).
    sequence = (
        await db.scalar(
            select(func.max(WorkflowRunEvent.sequence)).where(
                WorkflowRunEvent.tenant_id == run.tenant_id,
                WorkflowRunEvent.run_id == run.id,
            )
        )
        or 0
    ) + 1
    db.add(
        WorkflowRunEvent(
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_id=step.id if step else None,
            sequence=sequence,
            event_type=event_type,
            actor_user_id=actor_id,
            metadata_json=metadata or {},
        )
    )
    run.version += 1
    run.updated_at = datetime.now(timezone.utc)
    await db.flush()


async def queue_run(db, run):
    return await enqueue_job(
        db,
        tenant_id=run.tenant_id,
        kind="workflow_run",
        idempotency_key=f"workflow-run:{run.id}:v{run.version}",
        payload={"run_id": str(run.id), "run_version": run.version},
    )


async def submit_run(context, body):
    db = context.db
    key = f"workflow-run:{context.tenant_id}:{context.actor_user_id}:{body.request_id}"
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key}
    )
    request_hash = digest_payload(body.model_dump(mode="json"))
    existing = await db.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.tenant_id == context.tenant_id,
            WorkflowRun.actor_user_id == context.actor_user_id,
            WorkflowRun.request_id == body.request_id,
        )
        .with_for_update()
    )
    if existing:
        if existing.request_sha256 != request_hash:
            raise CapabilityError(
                "idempotency_conflict",
                "This request identity already has a different plan",
            )
        await current_context(db, existing)
        return await describe_run(db, existing)
    metadata = plan_metadata(body)
    run = WorkflowRun(
        id=uuid.uuid4(),
        tenant_id=context.tenant_id,
        actor_user_id=context.actor_user_id,
        matter_id=body.matter_id,
        request_id=body.request_id,
        origin_channel=context.channel,
        grant_id=context.grant_id,
        client_id=context.client_id,
        scope_snapshot=sorted(context.granted_scopes or []),
        objective=body.objective,
        plan_json=metadata,
        plan_sha256=digest_payload(metadata),
        request_sha256=request_hash,
        status="queued",
        version=1,
        next_step=0,
    )
    if context.allowed_sources:
        run.source_context_ciphertext, run.source_context_sha256 = seal_payload(
            context.allowed_sources,
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_id=run.id,
            kind="sources",
        )
    for item in body.steps:
        await current_context(db, run, resolve_capability_spec(item.capability))
    await verify_source_bindings(db, run)
    db.add(run)
    await db.flush()
    for position, item in enumerate(body.steps):
        step_id = uuid.uuid4()
        encrypted, fingerprint = seal_payload(
            item.arguments,
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_id=step_id,
            kind="arguments",
        )
        db.add(
            WorkflowRunStep(
                id=step_id,
                tenant_id=run.tenant_id,
                run_id=run.id,
                position=position,
                step_key=item.step_key,
                capability=item.capability,
                references_json={
                    key: ref.model_dump(mode="json")
                    for key, ref in item.references.items()
                },
                arguments_ciphertext=encrypted,
                arguments_sha256=fingerprint,
                status="pending",
                attempts=0,
            )
        )
    await append_event(
        db,
        run,
        "run_proposed",
        actor_id=context.actor_user_id,
        metadata={"plan_sha256": run.plan_sha256, "step_count": len(body.steps)},
    )
    await queue_run(db, run)
    return await describe_run(db, run)


async def load_run(context, run_id, *, lock=False):
    statement = select(WorkflowRun).where(
        WorkflowRun.tenant_id == context.tenant_id,
        WorkflowRun.id == run_id,
        WorkflowRun.actor_user_id == context.actor_user_id,
    )
    run = await context.db.scalar(statement.with_for_update() if lock else statement)
    if not run:
        raise CapabilityError("run_not_found", "Workflow run not found")
    await current_context(context.db, run)
    return run


async def describe_run(db, run):
    steps = (
        await db.scalars(
            select(WorkflowRunStep)
            .where(
                WorkflowRunStep.tenant_id == run.tenant_id,
                WorkflowRunStep.run_id == run.id,
            )
            .order_by(WorkflowRunStep.position)
        )
    ).all()
    events = (
        await db.scalars(
            select(WorkflowRunEvent)
            .where(
                WorkflowRunEvent.tenant_id == run.tenant_id,
                WorkflowRunEvent.run_id == run.id,
            )
            .order_by(WorkflowRunEvent.sequence.desc())
            .limit(100)
        )
    ).all()
    return {
        "run_id": str(run.id),
        "matter_id": str(run.matter_id),
        "objective": run.objective,
        "status": run.status,
        "version": run.version,
        "origin_channel": run.origin_channel,
        "service_rule_id": str(run.service_rule_id) if run.service_rule_id else None,
        "plan_sha256": run.plan_sha256,
        "next_step": run.next_step,
        "failure_code": run.failure_code,
        "created_at": run.created_at.isoformat(),
        "steps": [
            {
                "step_key": step.step_key,
                "capability": step.capability,
                "status": step.status,
                "attempts": step.attempts,
                "result": step.result_summary,
                "required_inputs": step.required_inputs or [],
                "failure_code": step.failure_code,
                "task_id": str(step.task_id) if step.task_id else None,
                "artifact_id": str(step.artifact_id) if step.artifact_id else None,
                "storage_operation_id": str(step.storage_operation_id)
                if step.storage_operation_id
                else None,
                "approval_id": str(step.approval_id) if step.approval_id else None,
            }
            for step in steps
        ],
        "events": [
            {
                "sequence": event.sequence,
                "type": event.event_type,
                "at": event.created_at.isoformat(),
                "metadata": event.metadata_json,
            }
            for event in reversed(events)
        ],
    }


async def resume_run(context, run_id, body):
    db = context.db
    run = await load_run(context, run_id, lock=True)
    if run.version != body.expected_version:
        raise CapabilityError(
            "run_version_conflict", "Reload this run before continuing"
        )
    if run.status not in {"awaiting_input", "awaiting_review", "blocked"}:
        raise CapabilityError(
            "run_not_paused", "This run cannot be continued from its current state"
        )
    if run.status == "blocked" and run.failure_code not in {
        "actor_unavailable",
        "actor_permission_changed",
        "origin_grant_unavailable",
        "inactive_tenant",
        "workflow_rate_limited",
        "workflow_budget_unavailable",
        "retry_limit_reached",
    }:
        raise CapabilityError(
            "new_plan_required", "Review a new plan for the changed evidence"
        )
    step = await db.scalar(
        select(WorkflowRunStep)
        .where(
            WorkflowRunStep.tenant_id == run.tenant_id,
            WorkflowRunStep.run_id == run.id,
            WorkflowRunStep.position == run.next_step,
        )
        .with_for_update()
    )
    await current_context(db, run, resolve_capability_spec(step.capability))
    await verify_source_bindings(db, run)
    if body.missing_arguments:
        if run.status != "awaiting_input" or not set(body.missing_arguments).issubset(
            set(step.required_inputs or [])
        ):
            raise CapabilityError(
                "immutable_run_arguments",
                "Only requested missing inputs may be supplied",
            )
        arguments = open_payload(
            step.arguments_ciphertext,
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_id=step.id,
            kind="arguments",
            expected_sha256=step.arguments_sha256,
        )
        if set(arguments) & set(body.missing_arguments):
            raise CapabilityError(
                "immutable_run_arguments", "Existing inputs cannot be replaced"
            )
        arguments.update(body.missing_arguments)
        step.arguments_ciphertext, step.arguments_sha256 = seal_payload(
            arguments,
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_id=step.id,
            kind="arguments",
        )
        step.required_inputs = []
        step.status = "pending"
    run.status = "queued"
    run.failure_code = None
    await append_event(
        db,
        run,
        "run_resumed",
        step=step,
        actor_id=context.actor_user_id,
        metadata={"supplied_fields": sorted(body.missing_arguments)},
    )
    await queue_run(db, run)
    return await describe_run(db, run)


async def cancel_run(context, run_id, expected_version):
    db = context.db
    run = await load_run(context, run_id, lock=True)
    if run.version != expected_version:
        raise CapabilityError(
            "run_version_conflict", "Reload this run before cancelling"
        )
    if run.status in {"completed", "cancelled"}:
        return await describe_run(db, run)
    run.status = "cancelled"
    run.completed_at = datetime.now(timezone.utc)
    await append_event(db, run, "run_cancelled", actor_id=context.actor_user_id)
    return await describe_run(db, run)
