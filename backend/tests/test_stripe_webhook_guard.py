"""Ordering and idempotency guarantees for Stripe webhook processing.

These cover the two properties the guard exists to provide: a retried delivery
must not re-run its handler, and an event that Stripe delivers out of order must
not overwrite newer state with older state.
"""

import pytest
from sqlalchemy import select

from app.models.stripe_webhook_event import StripeWebhookEvent
from app.services.stripe_webhook_guard import claim_event, ordering_object_id


class TestOrderingObjectId:
    def test_subscription_events_order_against_the_customer(self):
        """Not the subscription id.

        A firm that cancels and resubscribes receives a new subscription id, so
        a delayed deletion for the old one would have no newer event under its
        own id and would never look stale. The customer persists across the
        whole lifecycle.
        """
        assert (
            ordering_object_id(
                "customer.subscription.updated", {"id": "sub_1", "customer": "cus_1"}
            )
            == "cus_1"
        )
        assert (
            ordering_object_id(
                "customer.subscription.deleted", {"id": "sub_9", "customer": "cus_1"}
            )
            == "cus_1"
        )

    def test_invoice_events_order_against_the_customer(self):
        obj = {"id": "in_1", "subscription": "sub_1", "customer": "cus_1"}
        assert ordering_object_id("invoice.paid", obj) == "cus_1"

    def test_expanded_object_reference_is_unwrapped(self):
        obj = {"id": "in_1", "customer": {"id": "cus_7"}}
        assert ordering_object_id("invoice.paid", obj) == "cus_7"

    def test_unknown_event_type_has_no_ordering_key(self):
        assert ordering_object_id("customer.created", {"id": "cus_1"}) is None


@pytest.mark.asyncio
class TestClaimEvent:
    async def test_first_delivery_is_claimed_and_recorded(self, db_session):
        claim = await claim_event(
            db_session,
            event_id="evt_1",
            event_type="customer.subscription.updated",
            event_created=1000,
            object_id="sub_1",
        )
        assert claim.should_process is True

        stored = await db_session.scalar(
            select(StripeWebhookEvent).where(StripeWebhookEvent.event_id == "evt_1")
        )
        assert stored is not None
        assert stored.object_id == "sub_1"

    async def test_retried_delivery_is_not_processed_twice(self, db_session):
        first = await claim_event(
            db_session,
            event_id="evt_dup",
            event_type="customer.subscription.updated",
            event_created=1000,
            object_id="sub_dup",
        )
        assert first.should_process is True
        await db_session.commit()

        second = await claim_event(
            db_session,
            event_id="evt_dup",
            event_type="customer.subscription.updated",
            event_created=1000,
            object_id="sub_dup",
        )
        assert second.should_process is False
        assert second.reason == "duplicate"

    async def test_stale_event_delivered_late_is_refused(self, db_session):
        """A retried cancellation must not undo a later resubscription.

        This is the sequence that previously downgraded a paying firm and
        nulled its subscription id, disabling its own recovery path. Because
        both events order against the customer, it holds even when the
        resubscription carries a different subscription id.
        """
        newer = await claim_event(
            db_session,
            event_id="evt_active",
            event_type="customer.subscription.updated",
            event_created=2000,
            object_id="cus_race",
        )
        assert newer.should_process is True
        await db_session.commit()

        stale = await claim_event(
            db_session,
            event_id="evt_canceled",
            event_type="customer.subscription.deleted",
            event_created=1000,
            object_id="cus_race",
        )
        assert stale.should_process is False
        assert stale.reason == "stale"

    async def test_resubscription_with_a_new_subscription_id_is_still_ordered(
        self, db_session
    ):
        """The real Stripe shape: cancel and resubscribe issues a *new* id.

        Ordering by subscription id would treat these as unrelated objects and
        let the delayed deletion through.
        """
        resubscribed = await claim_event(
            db_session,
            event_id="evt_new_sub_active",
            event_type="customer.subscription.updated",
            event_created=5000,
            object_id=ordering_object_id(
                "customer.subscription.updated",
                {"id": "sub_new", "customer": "cus_resub"},
            ),
        )
        assert resubscribed.should_process is True
        await db_session.commit()

        delayed_delete = await claim_event(
            db_session,
            event_id="evt_old_sub_deleted",
            event_type="customer.subscription.deleted",
            event_created=4000,
            object_id=ordering_object_id(
                "customer.subscription.deleted",
                {"id": "sub_old", "customer": "cus_resub"},
            ),
        )
        assert delayed_delete.should_process is False
        assert delayed_delete.reason == "stale"

    async def test_newer_event_for_same_object_still_applies(self, db_session):
        await claim_event(
            db_session,
            event_id="evt_old",
            event_type="customer.subscription.updated",
            event_created=1000,
            object_id="sub_seq",
        )
        await db_session.commit()

        newer = await claim_event(
            db_session,
            event_id="evt_new",
            event_type="customer.subscription.updated",
            event_created=2000,
            object_id="sub_seq",
        )
        assert newer.should_process is True

    async def test_events_for_different_objects_do_not_block_each_other(
        self, db_session
    ):
        await claim_event(
            db_session,
            event_id="evt_a",
            event_type="customer.subscription.updated",
            event_created=5000,
            object_id="sub_a",
        )
        await db_session.commit()

        other = await claim_event(
            db_session,
            event_id="evt_b",
            event_type="customer.subscription.updated",
            event_created=1000,
            object_id="sub_b",
        )
        assert other.should_process is True

    async def test_event_without_ordering_key_is_still_deduplicated(self, db_session):
        first = await claim_event(
            db_session,
            event_id="evt_noobj",
            event_type="customer.created",
            event_created=1000,
            object_id=None,
        )
        assert first.should_process is True
        await db_session.commit()

        again = await claim_event(
            db_session,
            event_id="evt_noobj",
            event_type="customer.created",
            event_created=1000,
            object_id=None,
        )
        assert again.should_process is False
        assert again.reason == "duplicate"
