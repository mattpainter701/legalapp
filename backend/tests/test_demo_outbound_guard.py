"""Fail-closed boundaries for the disposable customer demo workspace."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.middleware.demo_quota import _is_blocked_demo_action
from app.models.task import Task, TaskAutomationRun, TaskEvent
from app.schemas.auth import ForgotPasswordRequest
from app.services.task_automation import (
    ActionApprovalConflict,
    DELIVERY_NOT_ATTEMPTED,
    automation_idempotency_key,
    enqueue_durable_automation,
    run_task_automation,
)
from .test_task_automation import _approved_email_task, _matter


# Each row is (method, registered route template, concrete request path). The
# template is asserted against the live route table so a guard prefix can never
# silently drift away from the router that actually mounts the endpoint.
BLOCKED_DEMO_ROUTES = [
    ("POST", "/api/calendar/sync", "/api/calendar/sync"),
    ("POST", "/api/calendar/scheduled-events", "/api/calendar/scheduled-events"),
    (
        "PATCH",
        "/api/calendar/scheduled-events/{event_id}",
        "/api/calendar/scheduled-events/123",
    ),
    (
        "DELETE",
        "/api/calendar/scheduled-events/{event_id}",
        "/api/calendar/scheduled-events/123",
    ),
    ("POST", "/api/email/calendar", "/api/email/calendar"),
    ("POST", "/api/email/scan", "/api/email/scan"),
    ("POST", "/api/email/draft-response", "/api/email/draft-response"),
    ("POST", "/api/matters/{matter_id}/email-client", "/api/matters/123/email-client"),
    (
        "POST",
        "/api/matters/{matter_id}/cloud-folder/sync",
        "/api/matters/123/cloud-folder/sync",
    ),
    (
        "POST",
        "/api/plugins/mediation/cases/{case_id}/assets/{asset_id}/send",
        "/api/plugins/mediation/cases/123/assets/456/send",
    ),
    (
        "POST",
        "/api/matters/{matter_id}/signatures/{request_id}/send",
        "/api/matters/123/signatures/456/send",
    ),
    (
        "POST",
        "/api/clients/{client_id}/sync/quickbooks",
        "/api/clients/123/sync/quickbooks",
    ),
    ("POST", "/api/mcp/product-keys", "/api/mcp/product-keys"),
    ("POST", "/api/integrations/zoom/disconnect", "/api/integrations/zoom/disconnect"),
    (
        "POST",
        "/api/intake/dashboard/zoom-phone/sync",
        "/api/intake/dashboard/zoom-phone/sync",
    ),
    (
        "POST",
        "/api/scheduler/agents/{agent_name}/run",
        "/api/scheduler/agents/user-sync/run",
    ),
    ("POST", "/api/billing/checkout-session", "/api/billing/checkout-session"),
    ("POST", "/api/billing/portal", "/api/billing/portal"),
    (
        "POST",
        "/api/billing/invoices/{invoice_id}/payment-link",
        "/api/billing/invoices/123/payment-link",
    ),
    ("POST", "/api/admin/users/invite", "/api/admin/users/invite"),
    (
        "POST",
        "/api/matters/{matter_id}/portal/invite",
        "/api/matters/123/portal/invite",
    ),
    ("POST", "/api/tasks/{task_id}/remind", "/api/tasks/123/remind"),
    ("POST", "/api/auth/forgot-password", "/api/auth/forgot-password"),
    (
        "POST",
        "/api/plugins/mediation/cases/{case_id}/parties/{party_id}/invite",
        "/api/plugins/mediation/cases/123/parties/456/invite",
    ),
]


@pytest.mark.parametrize(("method", "template", "path"), BLOCKED_DEMO_ROUTES)
def test_demo_outbound_routes_are_blocked(method, template, path):
    assert _is_blocked_demo_action(path, method)


def _registered_routes(routes, prefix: str = ""):
    """Flatten the app route table, including lazily included sub-routers."""
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            child_prefix = prefix + (getattr(context, "prefix", "") or "")
            yield from _registered_routes(included.routes, child_prefix)
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods:
            for verb in methods:
                yield (prefix + path, verb)


@pytest.mark.parametrize(("method", "template", "path"), BLOCKED_DEMO_ROUTES)
def test_demo_outbound_guard_targets_registered_routes(method, template, path):
    """A guarded path that no router serves is a fail-open guard, not a guard.

    The guard matches raw request paths, so a prefix that drifts from the
    router mounting the endpoint (for example ``/api/email-agent`` when the
    router is mounted at ``/api/email``) silently stops blocking anything.
    """
    from app.main import app

    assert (template, method) in set(_registered_routes(app.routes))


@pytest.mark.parametrize(
    "path",
    [
        "/api/tasks/123/transition",
        "/api/tasks/123/pending-action",
        "/api/matters",
        "/api/calendar/events",
        "/api/plugins/mediation/cases/123/assets/456/approve",
        "/api/clients",
        "/api/clients/import.csv",
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
        "/api/email/scan",
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


@pytest.mark.asyncio
async def test_demo_task_notifications_do_not_reach_calendar_or_email(
    db_session, test_tenant, test_user, monkeypatch
):
    from app.services import task_notifications

    task = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        created_by_user_id=test_user.id,
        assigned_to_user_id=test_user.id,
        title="Synthetic assigned task",
        status="open",
        due_date=None,
    )
    db_session.add(task)
    test_tenant.billing_tier = "demo"
    await db_session.commit()
    monkeypatch.setattr(
        task_notifications,
        "push_task_to_calendars",
        lambda *args, **kwargs: pytest.fail("demo calendar notification dispatched"),
    )

    result = await task_notifications.notify_task_created(
        db_session, task, str(test_tenant.id)
    )
    assert result.name == "NOT_REQUIRED"


@pytest.mark.asyncio
async def test_demo_tenant_is_excluded_from_all_background_scheduler_jobs(
    db_session, test_tenant
):
    from app.services.scheduler import _run_for_active_tenants

    test_tenant.billing_tier = "demo"
    await db_session.commit()
    calls = []

    async def scheduled_provider_action():
        calls.append("called")

    results = await _run_for_active_tenants(scheduled_provider_action)

    assert results == []
    assert calls == []


@pytest.mark.asyncio
async def test_demo_password_reset_returns_non_enumerating_success_without_delivery(
    db_session, test_tenant, test_user, monkeypatch
):
    from app.routers import auth

    test_tenant.billing_tier = "demo"
    test_user.password_hash = "not-a-real-password-hash"
    await db_session.commit()
    redis_calls = []
    email_calls = []

    class FakeRedis:
        async def setex(self, *args):
            redis_calls.append(args)

    async def send_email(*args, **kwargs):
        email_calls.append((args, kwargs))

    monkeypatch.setattr(auth.email_service, "send_email", send_email)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=FakeRedis()))
    )

    response = await auth.forgot_password(
        ForgotPasswordRequest(email=test_user.email), request, db_session
    )

    assert response == {
        "message": "If that email exists, a reset link has been sent.",
        "reset_token": None,
    }
    assert redis_calls == []
    assert email_calls == []
