"""End-to-end behaviour of the Stripe webhook endpoints.

The unit tests around ``claim_event`` prove the guard in isolation. These prove
the dispatcher actually uses it: that a retried delivery is skipped, that an
event Stripe delivered out of order does not revert current state, that a
handler failure surfaces as 5xx so Stripe retries, and that an event which
applied no work releases its claim so a later replay can still be applied.
"""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.stripe_webhook_event import StripeWebhookEvent
from app.models.tenant import Tenant
from app.routers import billing

settings = get_settings()

WEBHOOK_PATH = "/api/billing/webhook"


def _event(event_id, event_type, created, obj):
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "data": {"object": obj},
    }


@pytest.fixture
def stripe_event(monkeypatch):
    """Feed a chosen event through signature verification.

    Signature verification itself is Stripe's library and is exercised by its
    own tests; what matters here is everything the dispatcher does afterwards.
    """
    holder = {}

    def _construct(payload, sig_header, secret):
        return holder["event"]

    monkeypatch.setattr(billing.stripe.Webhook, "construct_event", _construct)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dispatch", raising=False)
    monkeypatch.setattr(
        settings, "STRIPE_WEBHOOK_SECRET", "whsec_test_dispatch", raising=False
    )

    def _set(event):
        holder["event"] = event

    return _set


async def _post(client, event, stripe_event):
    stripe_event(event)
    return await client.post(
        WEBHOOK_PATH, json={}, headers={"stripe-signature": "t=1,v1=test"}
    )


@pytest.mark.asyncio
class TestStripeWebhookDispatch:
    async def test_a_subscription_update_is_applied_and_recorded(
        self, client, db_session, test_tenant, stripe_event
    ):
        test_tenant.stripe_customer_id = "cus_dispatch_ok"
        await db_session.commit()

        response = await _post(
            client,
            _event(
                "evt_dispatch_ok",
                "customer.subscription.updated",
                1000,
                {
                    "id": "sub_ok",
                    "customer": "cus_dispatch_ok",
                    "status": "active",
                    "items": {"data": [{"plan": {"metadata": {"tier": "flat"}}}]},
                },
            ),
            stripe_event,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        await db_session.refresh(test_tenant)
        assert test_tenant.stripe_subscription_id == "sub_ok"
        assert test_tenant.billing_tier == "flat"

        recorded = await db_session.scalar(
            select(StripeWebhookEvent).where(
                StripeWebhookEvent.event_id == "evt_dispatch_ok"
            )
        )
        assert recorded is not None
        # Ordered against the customer, not the subscription.
        assert recorded.object_id == "cus_dispatch_ok"

    async def test_a_retried_delivery_is_skipped(
        self, client, db_session, test_tenant, stripe_event
    ):
        test_tenant.stripe_customer_id = "cus_dispatch_dup"
        await db_session.commit()

        event = _event(
            "evt_dispatch_dup",
            "customer.subscription.updated",
            1000,
            {
                "id": "sub_dup",
                "customer": "cus_dispatch_dup",
                "status": "active",
                "items": {"data": []},
            },
        )
        first = await _post(client, event, stripe_event)
        assert first.json()["status"] == "ok"

        second = await _post(client, event, stripe_event)
        assert second.status_code == 200
        assert second.json()["status"] == "skipped"
        assert second.json()["reason"] == "duplicate"

    async def test_a_late_cancellation_cannot_undo_a_resubscription(
        self, client, db_session, test_tenant, stripe_event
    ):
        """The failure this PR exists to prevent, end to end."""
        test_tenant.stripe_customer_id = "cus_dispatch_race"
        await db_session.commit()

        await _post(
            client,
            _event(
                "evt_dispatch_resub",
                "customer.subscription.updated",
                5000,
                {
                    "id": "sub_new",
                    "customer": "cus_dispatch_race",
                    "status": "active",
                    "items": {"data": [{"plan": {"metadata": {"tier": "flat"}}}]},
                },
            ),
            stripe_event,
        )

        late = await _post(
            client,
            _event(
                "evt_dispatch_late_cancel",
                "customer.subscription.deleted",
                4000,
                {"id": "sub_old", "customer": "cus_dispatch_race"},
            ),
            stripe_event,
        )
        assert late.status_code == 200
        assert late.json()["status"] == "skipped"
        assert late.json()["reason"] == "stale"

        await db_session.refresh(test_tenant)
        assert test_tenant.stripe_subscription_id == "sub_new"
        assert test_tenant.billing_tier == "flat"

    async def test_an_unknown_customer_releases_its_claim_for_replay(
        self, client, db_session, stripe_event
    ):
        response = await _post(
            client,
            _event(
                "evt_dispatch_unknown",
                "invoice.payment_failed",
                1000,
                {"customer": "cus_dispatch_never_seen"},
            ),
            stripe_event,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "unresolved"

        # No claim persisted, so replaying after the link is repaired applies.
        recorded = await db_session.scalar(
            select(StripeWebhookEvent).where(
                StripeWebhookEvent.event_id == "evt_dispatch_unknown"
            )
        )
        assert recorded is None

    async def test_a_handler_failure_is_not_reported_as_success(
        self, client, db_session, test_tenant, stripe_event, monkeypatch
    ):
        """Returning 200 here would stop Stripe retrying a recoverable error."""
        test_tenant.stripe_customer_id = "cus_dispatch_boom"
        await db_session.commit()

        async def _explode(db, obj):
            raise RuntimeError("transient database error")

        monkeypatch.setitem(
            billing._SUBSCRIPTION_HANDLERS, "invoice.paid", _explode
        )

        with pytest.raises(RuntimeError):
            await _post(
                client,
                _event(
                    "evt_dispatch_boom",
                    "invoice.paid",
                    1000,
                    {"customer": "cus_dispatch_boom"},
                ),
                stripe_event,
            )

    async def test_an_event_type_we_do_not_handle_is_acknowledged(
        self, client, stripe_event
    ):
        response = await _post(
            client,
            _event("evt_dispatch_ignored", "customer.created", 1000, {"id": "cus_x"}),
            stripe_event,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_a_missing_signature_header_is_refused(self, client, stripe_event):
        stripe_event(_event("evt_nosig", "customer.created", 1000, {}))
        response = await client.post(WEBHOOK_PATH, json={})
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_both_webhook_routes_share_one_subscription_dispatch_table():
    """The two endpoints must not drift apart in what they do with an event."""
    from app.routers import billing_extended

    assert billing_extended._SUBSCRIPTION_HANDLERS is billing._SUBSCRIPTION_HANDLERS


EXTENDED_WEBHOOK_PATH = "/api/billing/webhooks/stripe"


@pytest.fixture
def extended_stripe_event(monkeypatch):
    holder = {}

    def _construct(payload, sig_header, secret):
        return holder["event"]

    # `stripe` is the same module object in both routers, so patching the
    # library's Webhook here covers whichever import style each one uses.
    import stripe as stripe_module

    monkeypatch.setattr(stripe_module.Webhook, "construct_event", _construct)
    monkeypatch.setattr(
        settings, "STRIPE_WEBHOOK_SECRET", "whsec_test_dispatch", raising=False
    )

    def _set(event):
        holder["event"] = event

    return _set


async def _post_extended(client, event, extended_stripe_event):
    extended_stripe_event(event)
    return await client.post(
        EXTENDED_WEBHOOK_PATH, json={}, headers={"stripe-signature": "t=1,v1=test"}
    )


@pytest.mark.asyncio
class TestExtendedStripeWebhookDispatch:
    async def test_subscription_events_are_handled_identically_on_both_routes(
        self, client, db_session, test_tenant, extended_stripe_event
    ):
        test_tenant.stripe_customer_id = "cus_extended_ok"
        await db_session.commit()

        response = await _post_extended(
            client,
            _event(
                "evt_extended_ok",
                "customer.subscription.updated",
                1000,
                {
                    "id": "sub_extended",
                    "customer": "cus_extended_ok",
                    "status": "active",
                    "items": {"data": [{"plan": {"metadata": {"tier": "flat"}}}]},
                },
            ),
            extended_stripe_event,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "received"

        await db_session.refresh(test_tenant)
        assert test_tenant.stripe_subscription_id == "sub_extended"

    async def test_a_retried_delivery_is_skipped(
        self, client, db_session, test_tenant, extended_stripe_event
    ):
        test_tenant.stripe_customer_id = "cus_extended_dup"
        await db_session.commit()

        event = _event(
            "evt_extended_dup",
            "customer.subscription.updated",
            1000,
            {
                "id": "sub_extended_dup",
                "customer": "cus_extended_dup",
                "status": "active",
                "items": {"data": []},
            },
        )
        assert (await _post_extended(client, event, extended_stripe_event)).json()[
            "status"
        ] == "received"
        repeat = await _post_extended(client, event, extended_stripe_event)
        assert repeat.json()["status"] == "skipped"
        assert repeat.json()["reason"] == "duplicate"

    async def test_an_unknown_customer_releases_its_claim(
        self, client, db_session, extended_stripe_event
    ):
        response = await _post_extended(
            client,
            _event(
                "evt_extended_unknown",
                "invoice.payment_failed",
                1000,
                {"customer": "cus_extended_never_seen"},
            ),
            extended_stripe_event,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "unresolved"

        assert (
            await db_session.scalar(
                select(StripeWebhookEvent).where(
                    StripeWebhookEvent.event_id == "evt_extended_unknown"
                )
            )
        ) is None

    async def test_an_unhandled_event_type_is_acknowledged(
        self, client, extended_stripe_event
    ):
        response = await _post_extended(
            client,
            _event("evt_extended_ignored", "customer.created", 1000, {"id": "cus_y"}),
            extended_stripe_event,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "received"

    async def test_a_missing_signature_header_is_refused(
        self, client, extended_stripe_event
    ):
        extended_stripe_event(_event("evt_extended_nosig", "customer.created", 1, {}))
        response = await client.post(EXTENDED_WEBHOOK_PATH, json={})
        assert response.status_code == 400
