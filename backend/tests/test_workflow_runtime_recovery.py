from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import workflow_runtime as runtime
from app.services.automation_capabilities import (
    CapabilityError,
    resolve_capability_spec,
)
from app.services.task_automation import action_payload_sha256
from app.services.workflow_run_payloads import seal_payload


def state():
    run = NS(
        id=uuid4(), tenant_id=uuid4(), matter_id=uuid4(), status="running", next_step=0
    )
    step = NS(
        id=uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        task_id=uuid4(),
        artifact_id=None,
        capability="propose_client_email",
        action_sha256=action_payload_sha256({"type": "email_client"}),
        status="awaiting_review",
        storage_operation_id=None,
    )
    task = NS(
        id=step.task_id,
        status="review",
        pending_action={"type": "email_client"},
        review_policy="single",
    )
    return run, step, task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,certainty,reconcile,expected",
    [
        ("sent", "confirmed_sent", False, ("completed", None)),
        (
            "failed",
            "outcome_unknown",
            True,
            ("reconciliation_required", "delivery_outcome_unknown"),
        ),
        (
            "sending",
            "outcome_unknown",
            False,
            ("reconciliation_required", "delivery_outcome_unknown"),
        ),
        ("failed", "not_attempted", False, ("blocked", "reviewed_delivery_failed")),
        ("queued", "not_attempted", False, ("awaiting_review", None)),
        ("submitted", "provider_accepted", False, ("awaiting_review", None)),
    ],
)
async def test_resume_requires_confirmed_delivery_without_retry(
    status, certainty, reconcile, expected
):
    run, step, task = state()
    delivery = NS(
        status=status, delivery_certainty=certainty, reconciliation_required=reconcile
    )
    db = NS(scalar=AsyncMock(side_effect=[task, delivery]))
    assert await runtime._review_outcome(db, run, step) == expected


@pytest.mark.asyncio
async def test_changed_action_or_missing_task_cannot_resume():
    run, step, task = state()
    db = NS(scalar=AsyncMock(return_value=None))
    assert await runtime._review_outcome(db, run, step) == (
        "blocked",
        "review_task_unavailable",
    )
    task.pending_action = {"type": "email_client", "body": "changed"}
    db.scalar.side_effect = [task, None]
    assert await runtime._review_outcome(db, run, step) == (
        "blocked",
        "review_action_changed",
    )
    task.pending_action = {"type": "email_client"}
    db.scalar.side_effect = [task, None]
    assert await runtime._review_outcome(db, run, step) == ("awaiting_review", None)
    task.review_policy = "attorney_only"
    task.review_stage = "approved"
    task.attorney_approved_at = True
    task.attorney_reviewer_user_id = uuid4()
    task.attorney_approved_by_user_id = task.attorney_reviewer_user_id
    db.scalar.side_effect = [task, None]
    assert await runtime._review_outcome(db, run, step) == ("awaiting_review", None)


@pytest.mark.asyncio
async def test_plain_task_waits_for_human_transition():
    run, step, task = state()
    step.capability = "propose_task"
    db = NS(scalar=AsyncMock(return_value=task))
    assert await runtime._review_outcome(db, run, step) == ("awaiting_review", None)
    task.status = "completed"
    assert await runtime._review_outcome(db, run, step) == ("completed", None)


@pytest.mark.asyncio
async def test_artifact_revision_change_blocks_old_approval(monkeypatch):
    from app.services import work_artifact_reviews as reviews
    from app.services.task_workflow import TaskWorkflowError

    run, step, task = state()
    step.artifact_id = uuid4()
    step.artifact_revision_id = uuid4()
    db = NS(scalar=AsyncMock(side_effect=[task, uuid4()]))
    assert await runtime._review_outcome(db, run, step) == (
        "blocked",
        "artifact_revision_changed",
    )
    resolver = AsyncMock(side_effect=TaskWorkflowError("Awaiting attorney", 409))
    monkeypatch.setattr(reviews, "resolve_approved_attachment", resolver)
    db.scalar.side_effect = [task, step.artifact_revision_id]
    assert await runtime._review_outcome(db, run, step) == ("awaiting_review", None)
    resolver.side_effect = None
    approval = NS(approval_id=uuid4())
    resolver.return_value = (approval, object())
    db.scalar.side_effect = [task, step.artifact_revision_id]
    assert await runtime._review_outcome(db, run, step) == ("completed", None)
    assert step.approval_id == approval.approval_id


@pytest.mark.asyncio
async def test_stale_claim_is_not_allowed_to_checkpoint():
    job = NS(id=uuid4(), tenant_id=uuid4(), attempts=1, leased_at="original")
    db = NS(
        scalar=AsyncMock(
            return_value=NS(status="running", attempts=2, leased_at="newer")
        )
    )
    with pytest.raises(runtime.LostRunClaim):
        await runtime._lock_claim(db, job)
    db.scalar.return_value = NS(status="running", attempts=1, leased_at="original")
    assert await runtime._lock_claim(db, job) is db.scalar.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation_status", [None, "writing", "provider_accepted", "ambiguous", "linked"]
)
async def test_retry_exhaustion_preserves_uncertain_write(
    monkeypatch, operation_status
):
    run, step, task = state()
    job = NS(tenant_id=run.tenant_id, payload={"run_id": str(run.id)})
    if operation_status:
        step.storage_operation_id = uuid4()
    db = NS(
        scalar=AsyncMock(side_effect=[run, step]),
        get=AsyncMock(return_value=NS(status=operation_status)),
    )
    monkeypatch.setattr(runtime, "append_event", AsyncMock())
    await runtime.block_exhausted_run(db, job)
    assert run.status == (
        "reconciliation_required"
        if operation_status in {"writing", "provider_accepted", "ambiguous"}
        else "blocked"
    )
    assert run.failure_code == "retry_limit_reached"


@pytest.mark.asyncio
async def test_result_references_bind_only_completed_earlier_steps():
    run, step, task = state()
    step.position = 1
    step.step_key = "next"
    step.references_json = {"title": {"step_key": "prior", "path": ["title"]}}
    step.arguments_ciphertext, step.arguments_sha256 = seal_payload(
        {}, tenant_id=run.tenant_id, run_id=run.id, step_id=step.id, kind="arguments"
    )
    prior = NS(id=uuid4(), tenant_id=run.tenant_id, run_id=run.id)
    prior.result_ciphertext, prior.result_sha256 = seal_payload(
        {"title": "Reviewed title"},
        tenant_id=run.tenant_id,
        run_id=run.id,
        step_id=prior.id,
        kind="result",
    )
    db = NS(scalar=AsyncMock(return_value=prior))
    arguments = await runtime._arguments(
        db, run, step, resolve_capability_spec("propose_task")
    )
    assert arguments["title"] == "Reviewed title" and arguments["matter_id"] == str(
        run.matter_id
    )
    step.references_json["title"]["path"] = ["missing"]
    with pytest.raises(CapabilityError, match="does not contain"):
        await runtime._arguments(db, run, step, resolve_capability_spec("propose_task"))
    db.scalar.return_value = None
    with pytest.raises(CapabilityError, match="unavailable"):
        await runtime._arguments(db, run, step, resolve_capability_spec("propose_task"))
