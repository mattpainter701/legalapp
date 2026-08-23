"""MCP billing state transitions driven by Stripe subscription events.

These call the private handlers directly. Each handler mutates the session and
leaves the commit to its caller -- the webhook dispatcher commits once, so the
claim row recording the Stripe event and the state change it produced land in
the same transaction. Committing here reproduces that boundary before
``refresh()``, which would otherwise discard the pending changes and reload the
row unchanged.
"""

import pytest

from app.routers import billing


@pytest.mark.asyncio
async def test_active_subscription_enables_billing_but_not_entitlement(
    db_session, test_tenant
):
    test_tenant.stripe_customer_id = "cus_mcp_active"
    await db_session.commit()
    await billing._handle_subscription_updated(
        db_session,
        {
            "id": "sub_active",
            "customer": "cus_mcp_active",
            "status": "active",
            "items": {"data": []},
        },
    )
    await db_session.commit()
    await db_session.refresh(test_tenant)
    assert test_tenant.stripe_subscription_status == "active"
    assert test_tenant.mcp_billing_status == "active"
    assert test_tenant.mcp_entitlement_status == "disabled"


@pytest.mark.asyncio
async def test_first_payment_failure_suspends_product_key_traffic(
    db_session, test_tenant
):
    test_tenant.stripe_customer_id = "cus_mcp_failed"
    test_tenant.mcp_billing_status = "active"
    await db_session.commit()
    await billing._handle_payment_failed(
        db_session,
        {"customer": "cus_mcp_failed", "attempt_count": 1},
    )
    await db_session.commit()
    await db_session.refresh(test_tenant)
    assert test_tenant.stripe_subscription_status == "past_due"
    assert test_tenant.mcp_billing_status == "past_due"


@pytest.mark.asyncio
async def test_subscription_deletion_suspends_mcp_billing(db_session, test_tenant):
    test_tenant.stripe_customer_id = "cus_mcp_deleted"
    test_tenant.stripe_subscription_id = "sub_deleted"
    test_tenant.mcp_billing_status = "active"
    await db_session.commit()
    await billing._handle_subscription_deleted(
        db_session, {"customer": "cus_mcp_deleted"}
    )
    await db_session.commit()
    await db_session.refresh(test_tenant)
    assert test_tenant.stripe_subscription_id is None
    assert test_tenant.stripe_subscription_status == "canceled"
    assert test_tenant.mcp_billing_status == "suspended"
