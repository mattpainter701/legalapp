"""Idempotency and ordering guards for Stripe webhooks.

Stripe retries a webhook until it receives a 2xx and explicitly does not
guarantee delivery order. Two failure modes follow from that, and both were
live before this module existed:

1. A retried event re-runs its handler, duplicating any non-idempotent effect.
2. An older event delivered after a newer one overwrites current state with
   stale state -- e.g. a retried ``customer.subscription.deleted`` landing after
   the customer has already resubscribed, downgrading a paying firm.

``claim_event`` addresses both. It records the event before the handler runs and
refuses the claim when the event is a duplicate, or when a newer event for the
same Stripe object has already been applied.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stripe_webhook_event import StripeWebhookEvent

logger = logging.getLogger(__name__)

# Events whose ordering is decided per subscription/customer rather than per
# invoice. The value is the dotted path into the event object that identifies
# the thing whose state is being mutated.
# Every one of these events mutates *tenant-level* billing state, so they are
# ordered against the customer rather than the subscription.
#
# Ordering by subscription id looks natural and is wrong: when a firm cancels
# and resubscribes, Stripe issues a new subscription id. A delayed
# `customer.subscription.deleted` for the old id has no newer event under that
# id, so it would never be classified as stale, and would then clear billing
# state that a live subscription owns. The customer is the identity that
# persists across the whole lifecycle.
_OBJECT_ID_PATHS: dict[str, tuple[str, ...]] = {
    "customer.subscription.updated": ("customer",),
    "customer.subscription.deleted": ("customer",),
    "invoice.paid": ("customer",),
    "invoice.payment_succeeded": ("customer",),
    "invoice.payment_failed": ("customer",),
}


def ordering_object_id(event_type: str, obj: dict[str, Any]) -> str | None:
    """Identify which Stripe object an event's ordering should be judged against.

    Subscription and invoice lifecycle events all order against the customer,
    because they all write tenant-level state that outlives any one
    subscription id. See ``_OBJECT_ID_PATHS`` for why the subscription id is
    the wrong key.
    """
    for key in _OBJECT_ID_PATHS.get(event_type, ()):
        value = obj.get(key)
        if isinstance(value, dict):
            value = value.get("id")
        if isinstance(value, str) and value:
            return value
    return None


class StripeTargetUnresolved(Exception):
    """A handler could not identify the tenant or record an event refers to.

    Raised instead of returning quietly so the dispatcher can release the claim.
    A claim that survives an event which applied no work is worse than no claim:
    the operator backfills the missing metadata, replays the event from the
    Stripe dashboard, and the replay is rejected as a duplicate -- leaving the
    payment permanently unreconciled with no way back.
    """


class EventClaim:
    """Outcome of attempting to claim a Stripe event for processing."""

    __slots__ = ("should_process", "reason")

    def __init__(self, should_process: bool, reason: str = "") -> None:
        self.should_process = should_process
        self.reason = reason


async def claim_event(
    db: AsyncSession,
    *,
    event_id: str,
    event_type: str,
    event_created: int,
    object_id: str | None,
) -> EventClaim:
    """Claim an event for processing, or explain why it must be skipped.

    Returns ``should_process=False`` for a duplicate delivery and for an event
    that is older than one already applied to the same object. The caller should
    treat both as success and return 2xx -- there is nothing for Stripe to retry.

    The row is inserted before the handler runs so that a duplicate arriving
    concurrently loses the unique-constraint race rather than double-applying.
    """
    if object_id:
        # Serialize claims per object before reading the high-water mark.
        #
        # Without this, two events for the same customer delivered concurrently
        # both read "nothing newer" before either row is committed -- the unique
        # constraint covers event_id alone, so both inserts succeed, both
        # handlers run, and the older one can commit last and revert billing
        # state. That is precisely the reversion this guard exists to prevent.
        #
        # A transaction-scoped advisory lock keyed on the object id makes the
        # read-then-insert atomic against other claims for the same object,
        # while leaving unrelated customers fully concurrent. It is released
        # when the surrounding transaction ends, on commit or rollback.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"stripe_webhook_object:{object_id}"},
        )
        newest = await db.scalar(
            select(StripeWebhookEvent.event_created)
            .where(StripeWebhookEvent.object_id == object_id)
            .order_by(StripeWebhookEvent.event_created.desc())
            .limit(1)
        )
        if newest is not None and newest > event_created:
            logger.warning(
                "Stripe event %s (%s, created=%s) is older than the last applied "
                "event for object %s (created=%s); skipping to avoid reverting "
                "current state",
                event_id,
                event_type,
                event_created,
                object_id,
                newest,
            )
            return EventClaim(False, "stale")

    record = StripeWebhookEvent(
        event_id=event_id,
        event_type=event_type,
        object_id=object_id,
        event_created=event_created,
    )
    db.add(record)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        logger.info("Stripe event %s already processed; skipping", event_id)
        return EventClaim(False, "duplicate")

    return EventClaim(True)
