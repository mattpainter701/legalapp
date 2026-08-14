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


@pytest.mark.asyncio
async def test_approving_a_drafted_email_sends_it_once(
    db_session, test_tenant, test_user, monkeypatch
):
    sender = _RecordingSender()
    monkeypatch.setattr(task_automation, "email_service", sender)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(db_session, test_tenant, test_user, matter)

    await task_automation.run_task_automation(
        task.id, test_tenant.id, from_status="review", actor_user_id=test_user.id
    )

    assert len(sender.calls) == 1
    assert sender.calls[0]["to"] == ["ops@redwood.example"]

    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "succeeded"
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
            task.id, test_tenant.id, from_status="review", actor_user_id=test_user.id
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
        task.id, test_tenant.id, from_status="review", actor_user_id=test_user.id
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
        task.id, test_tenant.id, from_status="review", actor_user_id=test_user.id
    )
    sender.result = EmailDeliveryResult.SENT
    await task_automation.run_task_automation(
        task.id, test_tenant.id, from_status="review", actor_user_id=test_user.id
    )

    assert len(sender.calls) == 2
    run = await db_session.scalar(
        select(TaskAutomationRun).where(TaskAutomationRun.task_id == task.id)
    )
    assert run.status == "succeeded"


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
        task.id, test_tenant.id, from_status="review", actor_user_id=test_user.id
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
        task.id, test_tenant.id, from_status="in_progress", actor_user_id=test_user.id
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
        task.id, test_tenant.id, from_status="review", actor_user_id=test_user.id
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
        task.id, test_tenant.id, from_status="review", actor_user_id=test_user.id
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
        task.id, uuid.uuid4(), from_status="review", actor_user_id=test_user.id
    )

    assert sender.calls == []
