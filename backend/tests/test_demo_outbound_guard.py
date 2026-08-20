"""Fail-closed boundaries for the disposable customer demo workspace."""

import uuid

import pytest

from app.middleware.demo_quota import _is_blocked_demo_action
from app.models.task import Task
from app.services.task_automation import (
    ActionApprovalConflict,
    enqueue_durable_automation,
)


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
