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


@pytest.mark.asyncio
async def test_deleting_a_superseded_subscription_leaves_a_paying_firm_alone(
    db_session, test_tenant
):
    """A resubscription issues a new subscription id.

    Ordering cannot catch this on its own: the delayed deletion names a
    different subscription, so it is not a stale event about the same object.
    Identity has to be checked directly, or the firm that just resubscribed is
    suspended while paying.
    """
    test_tenant.stripe_customer_id = "cus_resubscribed"
    test_tenant.stripe_subscription_id = "sub_new"
    test_tenant.stripe_subscription_status = "active"
    test_tenant.billing_tier = "flat"
    test_tenant.mcp_billing_status = "active"
    await db_session.commit()

    await billing._handle_subscription_deleted(
        db_session, {"id": "sub_old", "customer": "cus_resubscribed"}
    )
    await db_session.commit()
    await db_session.refresh(test_tenant)

    assert test_tenant.stripe_subscription_id == "sub_new"
    assert test_tenant.stripe_subscription_status == "active"
    assert test_tenant.billing_tier == "flat"
    assert test_tenant.mcp_billing_status == "active"


@pytest.mark.asyncio
async def test_deleting_the_current_subscription_still_suspends(db_session, test_tenant):
    test_tenant.stripe_customer_id = "cus_current"
    test_tenant.stripe_subscription_id = "sub_current"
    test_tenant.mcp_billing_status = "active"
    await db_session.commit()

    await billing._handle_subscription_deleted(
        db_session, {"id": "sub_current", "customer": "cus_current"}
    )
    await db_session.commit()
    await db_session.refresh(test_tenant)

    assert test_tenant.stripe_subscription_id is None
    assert test_tenant.stripe_subscription_status == "canceled"
    assert test_tenant.mcp_billing_status == "suspended"


@pytest.mark.asyncio
async def test_terminal_update_for_a_superseded_subscription_is_ignored(
    db_session, test_tenant
):
    test_tenant.stripe_customer_id = "cus_terminal"
    test_tenant.stripe_subscription_id = "sub_live"
    test_tenant.stripe_subscription_status = "active"
    test_tenant.billing_tier = "flat"
    await db_session.commit()

    await billing._handle_subscription_updated(
        db_session,
        {
            "id": "sub_dead",
            "customer": "cus_terminal",
            "status": "canceled",
            "items": {"data": []},
        },
    )
    await db_session.commit()
    await db_session.refresh(test_tenant)

    assert test_tenant.stripe_subscription_id == "sub_live"
    assert test_tenant.billing_tier == "flat"


@pytest.mark.asyncio
async def test_unknown_customer_raises_so_the_claim_can_be_released(db_session):
    from app.services.stripe_webhook_guard import StripeTargetUnresolved

    with pytest.raises(StripeTargetUnresolved):
        await billing._handle_payment_failed(
            db_session, {"customer": "cus_never_seen", "attempt_count": 1}
        )


def test_log_references_do_not_carry_the_stripe_identifier():
    """Logs are shipped and retained; identifiers that name a paying firm
    should not travel with them, not even truncated.

    `_mask` keeps a prefix and suffix, which is fine behind authentication in
    an API response but still leaks most of a Stripe id's distinguishing
    characters into a log pipeline.
    """
    customer_id = "cus_ABC123DEF456GHI789"
    ref = billing._log_ref(customer_id)

    assert customer_id not in ref
    assert "cus_" not in ref
    # No meaningful substring of the original survives.
    assert not any(part in ref for part in (customer_id[:8], customer_id[-4:]))
    # Stable, so an operator can group lines about the same customer.
    assert ref == billing._log_ref(customer_id)
    assert ref != billing._log_ref("cus_somethingelse")
    assert billing._log_ref(None) == "none"


def test_api_responses_still_show_a_correlatable_masked_id():
    masked = billing._mask("cus_ABC123DEF456GHI789")
    assert masked is not None
    assert masked.startswith("cus_ABC1")
    assert masked.endswith("I789")
    assert billing._mask(None) is None
