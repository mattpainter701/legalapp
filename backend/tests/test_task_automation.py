"""Tests for deterministic, exactly-once execution of approved task actions.

The guarantee under test: approving a drafted client email sends it once, no
matter how many times the approval arrives. Everything else in the chat action
layer is recoverable; emailing a client twice is not.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.models.plugin import Matter
from app.models.task import Task, TaskAutomationRun
from app.services import task_automation
from app.services.email import EmailDeliveryResult


class _RecordingSender:
    """Stand-in for EmailService.send_email that counts real sends."""

    def __init__(self, result=EmailDeliveryResult.SENT):
        self.result = result
        self.calls = []

    async def send_email(self, to, subject, html_body, text_body=""):
        self.calls.append(
            {"to": list(to), "subject": subject, "html": html_body, "text": text_body}
        )
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def _matter(db_session, tenant, user):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        user_id=user.id,
        slug=f"matter-{uuid.uuid4().hex[:8]}",
        matter_name="Redwood Outdoor Supply - OGC Retainer",
        matter_type="corporate",
    )
    db_session.add(matter)
    await db_session.commit()
    return matter


async def _approved_email_task(db_session, tenant, user, matter, *, version=1):
    task = Task(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        matter_id=matter.id,
        title="Request insurance certificate from Redwood",
        status="review",
        reviewer_user_id=user.id,
        source="assistant",
        version=version,
        pending_action={
            "type": "email_client",
            "to": ["ops@redwood.example"],
            "subject": "Insurance certificate for the warehouse pilot",
            "body": "Please send the current certificate of insurance.",
            "matter_id": str(matter.id),
            "source_ids": [],
        },
    )
    db_session.add(task)
    await db_session.commit()
    return task


async def _drain_background_tasks() -> None:
    """Let _fire_and_log's detached task finish before asserting on sends.

    Execution is intentionally post-commit and out-of-band, so an HTTP response
    can return before the send runs. Awaiting the pending tasks keeps these
    assertions deterministic instead of racing the event loop.
    """
    import asyncio

    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    ]
    if pending:
        await asyncio.wait(pending, timeout=5)


@pytest.mark.asyncio
async def test_approving_a_drafted_email_sends_it_once(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert len(sender.calls) == 1
    assert sender.calls[0]["to"] == ["ops@redwood.example"]

    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "sent"
    assert run.error_message is None
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_repeated_approval_never_sends_a_second_copy(
    db_session, test_tenant, test_user, monkeypatch
):
    """A double-clicked Approve must not email the client twice."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    for _ in range(3):
        await task_automation.run_task_automation(
            task.id,
            test_tenant.id,
            from_status="review",
            to_status="in_progress",
            actor_user_id=test_user.id,
        )

    assert len(sender.calls) == 1
    run_count = await db_session.scalar(
        select(func.count())
        .select_from(TaskAutomationRun)
        .where(TaskAutomationRun.task_id == task.id)
    )
    assert run_count == 1


@pytest.mark.asyncio
async def test_concurrent_approvals_resolve_to_one_send(
    db_session, test_tenant, test_user, monkeypatch
):
    """Two racing transitions must collide on the claim, not both send."""
    import asyncio

    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await asyncio.gather(
        *(
            task_automation.run_task_automation(
                task.id,
                test_tenant.id,
                from_status="review",
                to_status="in_progress",
                actor_user_id=test_user.id,
            )
            for _ in range(4)
        )
    )

    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_unconfigured_delivery_is_not_recorded_as_sent(
    db_session, test_tenant, test_user, monkeypatch
):
    """A disabled mailer must never look like the client was contacted.

    EmailDeliveryResult is deliberately not a boolean for exactly this reason;
    recording UNCONFIGURED as success would both mislead the attorney and block
    the retry once delivery is fixed.
    """
    sender = _RecordingSender(result=EmailDeliveryResult.UNCONFIGURED)
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert "unconfigured" in (run.error_message or "").lower()

    # The draft survives so the approval can be retried after configuration.
    await db_session.refresh(task)
    assert task.pending_action is not None


@pytest.mark.asyncio
async def test_a_failed_run_can_be_retried_and_then_succeeds(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender(result=EmailDeliveryResult.FAILED)
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )
    sender.result = EmailDeliveryResult.SENT
    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert len(sender.calls) == 2
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "sent"


@pytest.mark.asyncio
async def test_a_raising_handler_records_failure_without_corrupting_the_task(
    db_session, test_tenant, test_user, monkeypatch
):
    """The approval already committed; a send failure must not undo it."""
    sender = _RecordingSender(result=RuntimeError("smtp exploded"))
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "failed"
    assert "smtp exploded" in run.error_message

    await db_session.refresh(task)
    assert task.status == "review"
    assert task.pending_action is not None


@pytest.mark.asyncio
async def test_automation_only_runs_on_approval_out_of_review(
    db_session, test_tenant, test_user, monkeypatch
):
    """A later manual transition must not re-trigger a send."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="in_progress",
        to_status="completed",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []
    run_count = await db_session.scalar(
        select(func.count())
        .select_from(TaskAutomationRun)
        .where(TaskAutomationRun.task_id == task.id)
    )
    assert run_count == 0


@pytest.mark.asyncio
async def test_a_task_without_a_pending_action_sends_nothing(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        created_by_user_id=test_user.id,
        matter_id=matter.id,
        title="Ordinary reviewed task",
        status="review",
        source="assistant",
    )
    db_session.add(task)
    await db_session.commit()

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []


@pytest.mark.asyncio
async def test_an_unknown_action_type_fails_closed(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        created_by_user_id=test_user.id,
        matter_id=matter.id,
        title="Unsupported action",
        status="review",
        source="assistant",
        pending_action={"type": "wire_transfer", "amount": "100000"},
    )
    db_session.add(task)
    await db_session.commit()

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []
    run_count = await db_session.scalar(
        select(func.count())
        .select_from(TaskAutomationRun)
        .where(TaskAutomationRun.task_id == task.id)
    )
    assert run_count == 0


@pytest.mark.asyncio
async def test_another_tenant_cannot_trigger_this_tenants_automation(
    db_session, test_tenant, test_user, monkeypatch
):
    """The tenant id is part of the lookup, not just the RLS context."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        uuid.uuid4(),
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    assert sender.calls == []


@pytest.mark.asyncio
async def test_editing_a_draft_cannot_change_its_recipients(
    client, db_session, test_tenant, test_user
):
    """The edit endpoint must not reopen what recipient resolution closed."""
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    response = await client.patch(
        f"/api/tasks/{task.id}/pending-action",
        json={
            "body": "Revised: please send it by Friday.",
            "to": ["attacker@evil.example"],
        },
    )

    assert response.status_code == 200
    await db_session.refresh(task)
    assert task.pending_action["body"] == "Revised: please send it by Friday."
    # The unknown field was ignored, not applied.
    assert task.pending_action["to"] == ["ops@redwood.example"]


@pytest.mark.asyncio
async def test_editing_a_draft_rotates_the_idempotency_key(
    db_session, test_tenant, test_user
):
    """An edited draft may send again; an unchanged one may not.

    Editing bumps task.version, which the key is derived from. Without this a
    failed-then-corrected draft could never be sent.
    """
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, version=1
    )

    first_key = task_automation.automation_idempotency_key(task, "review")
    task.version = 2
    second_key = task_automation.automation_idempotency_key(task, "review")

    assert first_key != second_key


@pytest.mark.asyncio
async def test_an_approved_draft_can_no_longer_be_edited(
    client, db_session, test_tenant, test_user
):
    """Editing after the fact would misrepresent what was actually sent."""
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    task.status = "in_progress"
    await db_session.commit()

    response = await client.patch(
        f"/api/tasks/{task.id}/pending-action",
        json={"body": "Too late."},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_another_tenant_cannot_edit_this_firms_draft(
    client, db_session, test_tenant, test_user
):
    from app.models.tenant import Tenant

    other = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain=f"other-{uuid.uuid4().hex[:8]}.com",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other)
    await db_session.flush()
    foreign_task = Task(
        id=uuid.uuid4(),
        tenant_id=other.id,
        title="Their drafted email",
        status="review",
        source="assistant",
        pending_action={
            "type": "email_client",
            "to": ["their-client@other.example"],
            "subject": "Confidential",
            "body": "Confidential.",
            "matter_id": str(uuid.uuid4()),
            "source_ids": [],
        },
    )
    db_session.add(foreign_task)
    await db_session.commit()

    response = await client.patch(
        f"/api/tasks/{foreign_task.id}/pending-action",
        json={"body": "Injected content."},
    )

    assert response.status_code == 404
    await db_session.refresh(foreign_task)
    assert foreign_task.pending_action["body"] == "Confidential."


@pytest.mark.parametrize("to_status", ["cancelled", "waiting", "completed", "review"])
@pytest.mark.asyncio
async def test_only_approval_into_active_work_sends(
    db_session, test_tenant, test_user, monkeypatch, to_status
):
    """Leaving Review is not the same as approving.

    Cancelling a drafted client email must never send it — that is the worst
    possible inversion of the attorney's intent. Nor should parking it in
    Waiting, or marking the task done without acting on the draft.
    """
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status=to_status,
        actor_user_id=test_user.id,
    )

    assert sender.calls == []
    run_count = await db_session.scalar(
        select(func.count())
        .select_from(TaskAutomationRun)
        .where(TaskAutomationRun.task_id == task.id)
    )
    assert run_count == 0


@pytest.mark.asyncio
async def test_cancelling_a_drafted_email_over_http_sends_nothing(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """End-to-end guard on the real approval surface."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={
            "to_status": "cancelled",
            "expected_version": task.version,
            "reason": "Client called instead; no letter needed.",
        },
    )

    assert response.status_code == 200
    # Must drain: asserting before the detached task runs would pass even when
    # the send does happen, which is exactly how this bug stayed invisible.
    await _drain_background_tasks()
    assert sender.calls == []


@pytest.mark.asyncio
async def test_approving_over_http_does_send(
    client, db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "in_progress", "expected_version": task.version},
    )

    assert response.status_code == 200
    await _drain_background_tasks()
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_the_generic_patch_cannot_approve_without_executing(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """Both status-changing endpoints must agree on what approval means.

    Before this was centralized, PATCH could move a task out of Review — which
    the board treats as approval — while silently never running the action, so
    an attorney would believe a client had been emailed.
    """
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    response = await client.patch(
        f"/api/tasks/{task.id}",
        json={"status": "in_progress", "expected_version": task.version},
    )

    assert response.status_code == 200
    await _drain_background_tasks()
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_approval_records_a_queued_delivery_in_the_same_transaction(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """Durability: the send intent must be committed with the approval.

    A process that dies between the two previously lost the send entirely while
    telling the attorney the work was approved.
    """
    from app.models.durable_job import DurableJob
    from app.services.task_automation import TASK_AUTOMATION_JOB

    # Delivery never runs, standing in for a worker that has not drained yet.
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    monkeypatch.setattr(
        task_automation, "dispatch_task_automation_if_approved", lambda *a, **k: False
    )
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    response = await client.post(
        f"/api/tasks/{task.id}/transition",
        json={"to_status": "in_progress", "expected_version": task.version},
    )
    assert response.status_code == 200

    # A durable job survives the process, so the send is recoverable.
    job = await db_session.scalar(
        select(DurableJob).where(
            DurableJob.tenant_id == test_tenant.id,
            DurableJob.kind == TASK_AUTOMATION_JOB,
        )
    )
    assert job is not None
    assert job.payload["task_id"] == str(task.id)


@pytest.mark.asyncio
async def test_the_worker_delivers_a_queued_send(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    class _Job:
        tenant_id = test_tenant.id
        payload = {
            "task_id": str(task.id),
            "from_status": "review",
            "to_status": "in_progress",
            "actor_user_id": str(test_user.id),
        }

    result = await task_automation.run_task_automation_job(_Job())

    assert result["task_id"] == str(task.id)
    assert len(sender.calls) == 1
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "sent"


@pytest.mark.asyncio
async def test_the_worker_does_not_resend_after_an_immediate_attempt_succeeded(
    db_session, test_tenant, test_user, monkeypatch
):
    """Both mechanisms fire by design; exactly-once is what makes that safe."""
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    class _Job:
        tenant_id = test_tenant.id
        payload = {
            "task_id": str(task.id),
            "from_status": "review",
            "to_status": "in_progress",
            "actor_user_id": str(test_user.id),
        }

    await task_automation.run_task_automation_job(_Job())

    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_a_run_in_flight_is_never_claimed_twice(
    db_session, test_tenant, test_user
):
    """A 'sending' run must block a second claim, not just a 'sent' one."""
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)
    key = task_automation.automation_idempotency_key(task, "review")

    first = await task_automation._claim_run(
        db_session,
        task,
        action_type="email_client",
        idempotency_key=key,
        actor_user_id=test_user.id,
    )
    assert first is not None and first.status == "sending"

    second = await task_automation._claim_run(
        db_session,
        task,
        action_type="email_client",
        idempotency_key=key,
        actor_user_id=test_user.id,
    )
    assert second is None


@pytest.mark.asyncio
async def test_delivery_state_is_reported_on_the_task(
    client, db_session, test_tenant, test_user, monkeypatch
):
    """The attorney needs the real outcome, not just "approved"."""
    sender = _RecordingSender(result=EmailDeliveryResult.FAILED)
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id,
        test_tenant.id,
        from_status="review",
        to_status="in_progress",
        actor_user_id=test_user.id,
    )

    body = (await client.get(f"/api/tasks/{task.id}")).json()

    assert body["delivery"]["status"] == "failed"
    assert body["delivery"]["action_type"] == "email_client"
    assert body["delivery"]["error_message"]
