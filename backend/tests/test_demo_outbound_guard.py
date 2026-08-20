"""Fail-closed boundaries for the disposable customer demo workspace."""

import uuid

import pytest
from sqlalchemy import select

from app.middleware.demo_quota import _is_blocked_demo_action
from app.models.task import Task, TaskAutomationRun, TaskEvent
from app.services.task_automation import (
    ActionApprovalConflict,
    DELIVERY_NOT_ATTEMPTED,
    automation_idempotency_key,
    enqueue_durable_automation,
    run_task_automation,
)
from .test_task_automation import _approved_email_task, _matter


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/calendar/sync"),
        ("POST", "/api/calendar/scheduled-events"),
        ("PATCH", "/api/calendar/scheduled-events/123"),
        ("DELETE", "/api/calendar/scheduled-events/123"),
        ("POST", "/api/email-agent/calendar"),
        ("POST", "/api/email-agent/scan"),
        ("POST", "/api/email-agent/draft-response"),
        ("POST", "/api/matters/123/email-client"),
        ("POST", "/api/matters/123/cloud-folder/sync"),
        ("POST", "/api/plugins/mediation/cases/123/assets/456/send"),
        ("POST", "/api/matters/123/signatures/456/send"),
        ("POST", "/api/mcp/product-keys"),
        ("POST", "/api/integrations/zoom/disconnect"),
        ("POST", "/api/intake/dashboard/zoom-phone/sync"),
        ("POST", "/scheduler/agents/user-sync/run"),
        ("POST", "/api/billing/checkout-session"),
        ("POST", "/api/billing/portal"),
        ("POST", "/api/billing/invoices/123/payment-link"),
    ],
)
def test_demo_outbound_routes_are_blocked(method, path):
    assert _is_blocked_demo_action(path, method)


@pytest.mark.parametrize(
    "path",
    [
        "/api/tasks/123/transition",
        "/api/tasks/123/pending-action",
        "/api/matters",
        "/api/calendar/events",
        "/api/plugins/mediation/cases/123/assets/456/approve",
    ],
)
def test_demo_synthetic_review_routes_remain_available(path):
    assert not _is_blocked_demo_action(path, "POST")


@pytest.mark.parametrize(
    "path",
    [
        "/api/calendar/events",
        "/api/calendar/scheduled-events",
        "/api/mcp/tools",
        "/api/mcp/product-keys",
        "/api/integrations/zoom/status",
        "/api/email-agent/scan",
        "/api/billing/status",
        "/api/billing/invoices/123/export",
    ],
)
def test_demo_read_only_provider_surfaces_remain_available(path):
    assert not _is_blocked_demo_action(path, "GET")


@pytest.mark.asyncio
async def test_demo_approval_cannot_enqueue_outbound_action(
    db_session, test_tenant, test_user
):
    test_tenant.billing_tier = "demo"
    task = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        created_by_user_id=test_user.id,
        title="Synthetic client email",
        status="review",
        source="assistant",
        pending_action={"type": "email_client", "to": ["client@example.invalid"]},
    )
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(ActionApprovalConflict, match="disabled in demo"):
        await enqueue_durable_automation(
            db_session,
            task,
            from_status="review",
            to_status="in_progress",
            actor_user_id=test_user.id,
        )


@pytest.mark.asyncio
async def test_demo_worker_terminalizes_preexisting_queued_action_without_dispatch(
    db_session, test_tenant, test_user, monkeypatch
):
    from app.services import task_automation

    calls = []

    async def send_email(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(task_automation.email_service, "send_email", send_email)
    matter = await _matter(db_session, test_tenant, test_user)
    task = await _approved_email_task(
        db_session, test_tenant, test_user, matter, status="in_progress"
    )
    test_tenant.billing_tier = "demo"
    key = automation_idempotency_key(task, "review")
    db_session.add(
        TaskAutomationRun(
            tenant_id=test_tenant.id,
            task_id=task.id,
            action_type="email_client",
            idempotency_key=key,
            status="queued",
        )
    )
    await db_session.commit()

    await run_task_automation(
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
    assert run.delivery_certainty == DELIVERY_NOT_ATTEMPTED
    assert "disabled in demo" in run.delivery_detail
    assert calls == []
    event = await db_session.scalar(
        select(TaskEvent).where(
            TaskEvent.task_id == task.id,
            TaskEvent.event_type == "automation_blocked",
        )
    )
    assert event is not None
