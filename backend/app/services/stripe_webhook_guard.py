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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stripe_webhook_event import StripeWebhookEvent

logger = logging.getLogger(__name__)

# Events whose ordering is decided per subscription/customer rather than per
# invoice. The value is the dotted path into the event object that identifies
# the thing whose state is being mutated.
_OBJECT_ID_PATHS: dict[str, tuple[str, ...]] = {
    "customer.subscription.updated": ("id",),
    "customer.subscription.deleted": ("id",),
    "invoice.paid": ("subscription", "customer"),
    "invoice.payment_succeeded": ("subscription", "customer"),
    "invoice.payment_failed": ("subscription", "customer"),
}


def ordering_object_id(event_type: str, obj: dict[str, Any]) -> str | None:
    """Identify which Stripe object an event's ordering should be judged against.

    Subscription events order against the subscription. Invoice events order
    against the subscription they belong to, falling back to the customer when
    an invoice carries no subscription (one-off charges).
    """
    for key in _OBJECT_ID_PATHS.get(event_type, ()):
        value = obj.get(key)
        if isinstance(value, dict):
            value = value.get("id")
        if isinstance(value, str) and value:
            return value
    return None


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
