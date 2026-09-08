"""Checkpoint bounded read/propose capabilities; human approval owns final effects."""

import uuid
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select

from app.database import async_session_maker, set_tenant_context
from app.models.durable_job import DurableJob
from app.models.workflow_run import WorkflowRun, WorkflowRunStep
from app.models.task import Task, TaskAutomationRun
from app.services.automation_capabilities import (
    CapabilityError,
    resolve_capability_spec,
)
from app.services.workflow_run_authority import current_context, verify_source_bindings
from app.services.workflow_run_ledger import append_event
from app.services.workflow_run_contract import referenced_value
from app.services.workflow_run_payloads import (
    open_payload,
    seal_payload,
    result_summary,
    canonical_payload,
)


class LostRunClaim(RuntimeError):
    pass


async def block_exhausted_run(db, job):
    from app.models.document_storage_operation import DocumentStorageOperation

    run = await db.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.tenant_id == job.tenant_id,
            WorkflowRun.id == uuid.UUID(job.payload["run_id"]),
        )
        .with_for_update()
    )
    if not run or run.status in {"completed", "cancelled", "failed"}:
        return
    step = await db.scalar(
        select(WorkflowRunStep)
        .where(
            WorkflowRunStep.tenant_id == run.tenant_id,
            WorkflowRunStep.run_id == run.id,
            WorkflowRunStep.position == run.next_step,
        )
        .with_for_update()
    )
    if not step:
        if run.next_step == len(run.plan_json.get("steps", [])):
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            await append_event(db, run, "run_completed")
        return
    operation = (
        await db.get(DocumentStorageOperation, step.storage_operation_id)
        if step.storage_operation_id
        else None
    )
    uncertain = operation and operation.status in {
        "writing",
        "provider_accepted",
        "ambiguous",
    }
    await _pause(
        db,
        run,
        step,
        "reconciliation_required" if uncertain else "blocked",
        "retry_limit_reached",
    )


async def _lock_claim(db, job):
    current = await db.scalar(
        select(DurableJob)
        .where(
            DurableJob.tenant_id == job.tenant_id,
            DurableJob.id == job.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not current
        or current.status != "running"
        or current.attempts != job.attempts
        or current.leased_at != job.leased_at
    ):
        raise LostRunClaim("A newer durable worker owns this attempt")
    return current


async def _pause(db, run, step, status, code=None):
    run.status = status
    run.failure_code = code
    step.failure_code = code
    if status == "reconciliation_required":
        step.status = "uncertain"
    elif status == "blocked":
        step.status = "blocked"
    elif status in {"awaiting_input", "awaiting_review"}:
        step.status = status
    await append_event(
        db, run, status, step=step, metadata={"failure_code": code} if code else {}
    )


def _decode(step, kind):
    return open_payload(
        getattr(step, f"{kind}_ciphertext"),
        tenant_id=step.tenant_id,
        run_id=step.run_id,
        step_id=step.id,
        kind=kind,
        expected_sha256=getattr(step, f"{kind}_sha256"),
    )


async def _arguments(db, run, step, spec):
    arguments = _decode(step, "arguments")
    for name, reference in step.references_json.items():
        prior = await db.scalar(
            select(WorkflowRunStep).where(
                WorkflowRunStep.tenant_id == run.tenant_id,
                WorkflowRunStep.run_id == run.id,
                WorkflowRunStep.step_key == reference["step_key"],
                WorkflowRunStep.position < step.position,
                WorkflowRunStep.status == "completed",
            )
        )
        if not prior or not prior.result_ciphertext:
            raise CapabilityError(
                "result_reference_unavailable",
                "The earlier reviewed result is unavailable",
            )
        try:
            arguments[name] = referenced_value(
                _decode(prior, "result"), reference["path"]
            )
        except ValueError as error:
            raise CapabilityError(
                "result_reference_unavailable",
                "The earlier result does not contain this field",
            ) from error
    fields = spec.args_model.model_fields
    if "matter_id" in fields:
        arguments["matter_id"] = str(run.matter_id)
    if "client_request_id" in fields:
        arguments["client_request_id"] = str(uuid.uuid5(run.id, step.step_key))
    return arguments


async def _review_outcome(db, run, step):
    from app.services.work_artifact_reviews import resolve_approved_attachment
    from app.models.generated_artifact import (
        GeneratedArtifact,
        GeneratedArtifactRevision,
    )
    from app.services.task_workflow import TaskWorkflowError, staged_review_is_approved
    from app.services.task_automation import action_payload_sha256

    task = await db.scalar(
        select(Task)
        .where(
            Task.tenant_id == run.tenant_id,
            Task.id == step.task_id,
            Task.matter_id == run.matter_id,
        )
        .with_for_update()
    )
    if not task:
        return "blocked", "review_task_unavailable"
    if step.artifact_id:
        revision_id = await db.scalar(
            select(GeneratedArtifactRevision.id)
            .join(
                GeneratedArtifact,
                GeneratedArtifact.id == GeneratedArtifactRevision.artifact_id,
            )
            .where(
                GeneratedArtifact.tenant_id == run.tenant_id,
                GeneratedArtifact.id == step.artifact_id,
                GeneratedArtifactRevision.tenant_id == run.tenant_id,
                GeneratedArtifactRevision.revision_no
                == GeneratedArtifact.current_revision_no,
            )
        )
        if revision_id != step.artifact_revision_id:
            return "blocked", "artifact_revision_changed"
        try:
            approval, _document = await resolve_approved_attachment(
                db,
                tenant_id=run.tenant_id,
                matter_id=run.matter_id,
                artifact_id=step.artifact_id,
            )
        except TaskWorkflowError:
            return "awaiting_review", None
        if not approval:
            return "blocked", "artifact_review_evidence_missing"
        step.approval_id = approval.approval_id
        return "completed", None
    if step.capability == "propose_task":
        # Tasks without a legal action still require the existing human review
        # transition; creating a task is never equivalent to completing it.
        if task.status in {"in_progress", "completed"}:
            return "completed", None
        return "awaiting_review", None
    delivery = await db.scalar(
        select(TaskAutomationRun)
        .where(
            TaskAutomationRun.tenant_id == run.tenant_id,
            TaskAutomationRun.task_id == task.id,
            TaskAutomationRun.action_sha256 == step.action_sha256,
        )
        .order_by(TaskAutomationRun.created_at.desc())
        .limit(1)
    )
    if delivery:
        if (
            delivery.status == "sent"
            and delivery.delivery_certainty == "confirmed_sent"
        ):
            return "completed", None
        if (
            delivery.reconciliation_required
            or delivery.delivery_certainty == "outcome_unknown"
            and delivery.status in {"failed", "sending"}
        ):
            return "reconciliation_required", "delivery_outcome_unknown"
        if delivery.status == "failed":
            return "blocked", "reviewed_delivery_failed"
        return "awaiting_review", None
    if (
        task.pending_action
        and action_payload_sha256(task.pending_action) != step.action_sha256
    ):
        return "blocked", "review_action_changed"
    # Approval schedules delivery through the existing task worker. Never send
    # or retry from this runtime, including after an uncertain provider result.
    if staged_review_is_approved(task):
        return "awaiting_review", None
    return "awaiting_review", None


async def run_workflow_job(job):
    from app.services.chat_tools import handlers
    from app.services.task_automation import action_payload_sha256
    from app.models.document_storage_operation import DocumentStorageOperation
    from app.services.workspace_mcp_budgets import (
        enforce_workspace_runtime_budget,
        bounded_workspace_read_result,
    )

    run_id = uuid.UUID(job.payload["run_id"])
    async with async_session_maker() as db:
        for _ in range(13):
            await set_tenant_context(db, str(job.tenant_id))
            await _lock_claim(db, job)
            run = await db.scalar(
                select(WorkflowRun)
                .where(
                    WorkflowRun.tenant_id == job.tenant_id,
                    WorkflowRun.id == run_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if not run or run.status in {
                "completed",
                "cancelled",
                "failed",
                "reconciliation_required",
            }:
                return {
                    "run_id": str(run_id),
                    "outcome": run.status if run else "unavailable",
                }
            if run.status not in {"queued", "running"}:
                return {"run_id": str(run.id), "outcome": run.status}
            step = await db.scalar(
                select(WorkflowRunStep)
                .where(
                    WorkflowRunStep.tenant_id == run.tenant_id,
                    WorkflowRunStep.run_id == run.id,
                    WorkflowRunStep.position == run.next_step,
                )
                .with_for_update()
            )
            if not step:
                run.status = "completed"
                run.completed_at = datetime.now(timezone.utc)
                await append_event(db, run, "run_completed")
                await db.commit()
                return {"run_id": str(run.id), "outcome": "completed"}
            spec = resolve_capability_spec(step.capability)
            try:
                context = await current_context(db, run, spec)
                await verify_source_bindings(db, run)
                if step.result_ciphertext:
                    outcome, code = await _review_outcome(db, run, step)
                    if outcome != "completed":
                        await _pause(db, run, step, outcome, code)
                        await db.commit()
                        return {"run_id": str(run.id), "outcome": outcome}
                    step.status = "completed"
                    step.completed_at = datetime.now(timezone.utc)
                    await db.flush([step])
                    run.next_step += 1
                    await append_event(
                        db,
                        run,
                        "reviewed_step_completed",
                        step=step,
                        metadata={"approval_id": str(step.approval_id)}
                        if step.approval_id
                        else {},
                    )
                    await db.commit()
                    continue
                arguments = await _arguments(db, run, step, spec)
                try:
                    parsed = spec.args_model.model_validate(arguments)
                except ValidationError as error:
                    missing = sorted(
                        {
                            str(item["loc"][0])
                            for item in error.errors()
                            if item["type"] == "missing" and len(item["loc"]) == 1
                        }
                    )
                    if len(missing) != len(error.errors()):
                        raise CapabilityError(
                            "invalid_step_arguments",
                            "Review a new plan with valid capability inputs",
                        ) from error
                    step.required_inputs = missing
                    await _pause(db, run, step, "awaiting_input")
                    await db.commit()
                    return {"run_id": str(run.id), "outcome": "awaiting_input"}
                step.status = "executing"
                step.attempt_id = uuid.uuid4()
                step.attempts += 1
                step.started_at = datetime.now(timezone.utc)
                run.status = "running"
                attempt_id = step.attempt_id
                context.request_id = str(uuid.uuid5(run.id, step.step_key))
                context.idempotency_key = context.request_id
                if run.source_context_ciphertext:
                    context.allowed_sources = open_payload(
                        run.source_context_ciphertext,
                        tenant_id=run.tenant_id,
                        run_id=run.id,
                        step_id=run.id,
                        kind="sources",
                        expected_sha256=run.source_context_sha256,
                    )

                async def checkpoint(
                    *,
                    phase,
                    artifact_id=None,
                    revision_id=None,
                    task_id=None,
                    operation_id=None,
                ):
                    await db.flush()
                    await _lock_claim(db, job)
                    await db.refresh(run, with_for_update=True)
                    await db.refresh(step, with_for_update=True)
                    if run.status != "running" or step.attempt_id != attempt_id:
                        raise LostRunClaim(
                            "The run no longer owns this capability attempt"
                        )
                    if artifact_id:
                        step.artifact_id = artifact_id
                        step.artifact_revision_id = revision_id
                        step.task_id = task_id
                    if operation_id:
                        step.storage_operation_id = operation_id
                    await append_event(
                        db,
                        run,
                        phase,
                        step=step,
                        metadata={
                            "attempt_id": str(attempt_id),
                            "operation_id": str(operation_id),
                        }
                        if operation_id
                        else {"attempt_id": str(attempt_id)},
                    )
                    await db.commit()
                    await set_tenant_context(db, str(run.tenant_id))
                    await _lock_claim(db, job)
                    await db.refresh(run, with_for_update=True)
                    await db.refresh(step, with_for_update=True)
                    # Never issue provider I/O after a revoked or cancelled run.
                    if run.status != "running" or step.attempt_id != attempt_id:
                        raise LostRunClaim("The run was cancelled at its checkpoint")
                    await current_context(db, run, spec)

                context.runtime_checkpoint = checkpoint
                handler = getattr(handlers, spec.handler_name)
                await enforce_workspace_runtime_budget(run)
                if spec.effect.value == "read":
                    savepoint = await db.begin_nested()
                    try:
                        result = canonical_payload(await handler(context, parsed))
                        if run.origin_channel == "workspace_mcp":
                            bounded_workspace_read_result(result)
                    finally:
                        await savepoint.rollback()
                else:
                    result = canonical_payload(await handler(context, parsed))
                step.result_ciphertext, step.result_sha256 = seal_payload(
                    result,
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    step_id=step.id,
                    kind="result",
                )
                step.result_summary = result_summary(result)
                if spec.mutating:
                    task_id = result.get("task_id")
                    if not task_id:
                        raise CapabilityError(
                            "proposal_identity_missing",
                            "The proposal did not return its review task",
                        )
                    step.task_id = uuid.UUID(task_id)
                    if result.get("artifact_id"):
                        step.artifact_id = uuid.UUID(result["artifact_id"])
                        step.artifact_revision_id = uuid.UUID(
                            result["artifact_revision_id"]
                        )
                    task = await db.get(Task, step.task_id)
                    if task and task.pending_action:
                        step.action_sha256 = action_payload_sha256(task.pending_action)
                    await _pause(db, run, step, "awaiting_review")
                else:
                    step.status = "completed"
                    step.completed_at = datetime.now(timezone.utc)
                    await db.flush([step])
                    run.next_step += 1
                    await append_event(
                        db,
                        run,
                        "read_step_completed",
                        step=step,
                        metadata={"result_sha256": step.result_sha256},
                    )
                await db.commit()
                if spec.mutating:
                    return {"run_id": str(run.id), "outcome": "awaiting_review"}
            except CapabilityError as error:
                # A handler savepoint rolls back ordinary failed proposals.
                # Runtime cloud checkpoints deliberately survive that boundary.
                operation = (
                    await db.scalar(
                        select(DocumentStorageOperation).where(
                            DocumentStorageOperation.tenant_id == run.tenant_id,
                            DocumentStorageOperation.artifact_revision_id
                            == step.artifact_revision_id,
                        )
                    )
                    if step.artifact_revision_id
                    else None
                )
                uncertain = operation and operation.status in {
                    "writing",
                    "provider_accepted",
                    "ambiguous",
                }
                if operation:
                    step.storage_operation_id = operation.id
                outcome = "reconciliation_required" if uncertain else "blocked"
                await _pause(db, run, step, outcome, error.code)
                await db.commit()
                return {
                    "run_id": str(run.id),
                    "outcome": outcome,
                    "failure_code": error.code,
                }
        raise RuntimeError("Bounded run exceeded its maximum step count")
