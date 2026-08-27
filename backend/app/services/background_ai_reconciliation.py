"""Resolve Background Automations reservations whose outcome was never confirmed.

When a provider call times out or the transport fails after the request may have
been accepted, the broker marks the reservation ``unknown`` and it keeps holding
its worst-case estimate against every quota window. That is the safe immediate
choice — assuming the work was free is how a pool overspends — but leaving it
there means a burst of timeouts can hold budget it never actually cost for the
rest of the window.

This sweep closes that gap. For each ambiguous reservation it asks the gateway
what really happened:

* the gateway reports usage for the request  -> settle at the real cost;
* the gateway is certain nothing was billed  -> release the estimate;
* nothing conclusive before the age limit    -> keep holding the estimate, stop
  re-asking, and surface it to operators.

The last case is deliberate. An unresolvable reservation is not quietly
forgiven; it stays counted and becomes a visible number on the operator panel.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker
from app.models.background_ai_usage import BackgroundAIUsageReservation
from app.services.ai_price_card import get_price_card
from app.services.background_ai_quota import _enable_background_quota_scope


logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass(frozen=True)
class ProviderOutcome:
    """What the gateway can tell us about one ambiguous request."""

    #: True when the provider definitively did work we must pay for.
    billed: bool
    #: True when the provider definitively did no work at all.
    not_billed: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    model: str | None = None

    @classmethod
    def inconclusive(cls) -> "ProviderOutcome":
        return cls(billed=False, not_billed=False)


# A lookup returns None when it cannot answer. Injected so the sweep is testable
# without a gateway and so a deployment without spend-log access degrades to
# "hold the estimate and alert" rather than guessing.
#
# Correlation uses the request id *we* generated and sent as ``x-request-id``
# and ``Idempotency-Key``, because the ambiguous case is precisely the one where
# no provider response — and therefore no provider request id — came back.
ProviderLookup = Callable[[str, str], Awaitable[ProviderOutcome | None]]


@dataclass
class ReconciliationReport:
    scanned: int = 0
    settled: int = 0
    released: int = 0
    still_unknown: int = 0
    aged_out: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "settled": self.settled,
            "released": self.released,
            "still_unknown": self.still_unknown,
            "aged_out": self.aged_out,
        }


async def _default_lookup(
    correlation_id: str, route_alias: str
) -> ProviderOutcome | None:
    """No gateway spend-log integration is configured yet.

    Returning ``None`` keeps every ambiguous reservation holding its estimate,
    which is the conservative outcome. Wire a real lookup here once the gateway
    exposes per-request spend, and reconciliation starts recovering capacity
    instead of only reporting it.
    """

    return None


async def reconcile_unknown_reservations(
    db: AsyncSession | None = None,
    *,
    lookup: ProviderLookup | None = None,
    session_factory=async_session_maker,
    now: datetime | None = None,
) -> ReconciliationReport:
    """Resolve or age out ambiguous reservations. Safe to run repeatedly."""

    moment = now or datetime.now(timezone.utc)
    resolve = lookup or _default_lookup
    grace = timedelta(minutes=max(1, settings.BACKGROUND_AI_RECONCILE_GRACE_MINUTES))
    max_age = timedelta(hours=max(1, settings.BACKGROUND_AI_RECONCILE_MAX_AGE_HOURS))
    batch = max(1, min(1000, settings.BACKGROUND_AI_RECONCILE_BATCH))
    report = ReconciliationReport()

    owns_session = db is None
    session = db or session_factory()
    try:
        await _enable_background_quota_scope(session)
        rows = (
            (
                await session.execute(
                    select(BackgroundAIUsageReservation)
                    .where(
                        BackgroundAIUsageReservation.status == "unknown",
                        BackgroundAIUsageReservation.reconciled_at.is_(None),
                        # Give an in-flight provider a chance to finish before
                        # treating silence as an answer.
                        BackgroundAIUsageReservation.created_at <= moment - grace,
                    )
                    .order_by(BackgroundAIUsageReservation.created_at)
                    .limit(batch)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return report

        price_card = await get_price_card(session)
        for row in rows:
            report.scanned += 1
            outcome: ProviderOutcome | None = None
            # A timed-out request has no provider request id — that is what made
            # it ambiguous — so correlate on the id we sent with the request.
            correlation_id = row.provider_request_id or row.request_id
            if correlation_id:
                try:
                    outcome = await resolve(correlation_id, row.route_alias or "")
                except Exception:
                    # A failing lookup must never abort the sweep or, worse,
                    # release capacity it could not actually verify.
                    logger.warning(
                        "background_ai.reconcile_lookup_failed",
                        extra={"reservation_id": str(row.id)},
                        exc_info=True,
                    )
                    outcome = None

            aged_out = row.created_at is not None and row.created_at <= moment - max_age

            if outcome is not None and outcome.billed:
                actual = price_card.actual_micros(
                    model=outcome.model or row.route_alias or "",
                    tokens_in=outcome.tokens_in,
                    tokens_out=outcome.tokens_out,
                )
                await _apply(
                    session,
                    row.id,
                    status="settled",
                    tokens_in=outcome.tokens_in,
                    tokens_out=outcome.tokens_out,
                    actual_micros=actual,
                    error_code="reconciled_billed",
                    moment=moment,
                )
                report.settled += 1
            elif outcome is not None and outcome.not_billed:
                await _apply(
                    session,
                    row.id,
                    status="released",
                    actual_micros=0,
                    error_code="reconciled_not_billed",
                    moment=moment,
                )
                report.released += 1
            elif aged_out:
                # Unresolvable. Stop re-asking, keep holding the estimate, and
                # let the operator panel show it as retained spend.
                await session.execute(
                    update(BackgroundAIUsageReservation)
                    .where(BackgroundAIUsageReservation.id == row.id)
                    .values(
                        reconciled_at=moment,
                        reconcile_attempts=(row.reconcile_attempts or 0) + 1,
                        error_code="reconcile_unresolved",
                    )
                )
                report.aged_out += 1
            else:
                await session.execute(
                    update(BackgroundAIUsageReservation)
                    .where(BackgroundAIUsageReservation.id == row.id)
                    .values(reconcile_attempts=(row.reconcile_attempts or 0) + 1)
                )
                report.still_unknown += 1

        await session.commit()
        if report.settled or report.released or report.aged_out:
            logger.info("background_ai.reconciled", extra=report.as_dict())
        return report
    finally:
        if owns_session:
            await session.close()


async def _apply(
    session: AsyncSession,
    reservation_id: uuid.UUID,
    *,
    status: str,
    actual_micros: int,
    error_code: str,
    moment: datetime,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    await session.execute(
        update(BackgroundAIUsageReservation)
        .where(
            BackgroundAIUsageReservation.id == reservation_id,
            # Only ever transition a row this sweep still owns.
            BackgroundAIUsageReservation.status == "unknown",
        )
        .values(
            status=status,
            tokens_in=max(0, int(tokens_in)),
            tokens_out=max(0, int(tokens_out)),
            actual_micros=max(0, int(actual_micros)),
            error_code=error_code,
            reconciled_at=moment,
            settled_at=moment,
        )
    )


async def unreconciled_summary(db: AsyncSession, *, pool: str) -> dict[str, Any]:
    """Operator-facing count of reservations still holding unverified spend."""

    await _enable_background_quota_scope(db)
    rows = (
        await db.execute(
            select(
                BackgroundAIUsageReservation.error_code,
                BackgroundAIUsageReservation.id,
            ).where(
                BackgroundAIUsageReservation.pool == pool,
                BackgroundAIUsageReservation.status == "unknown",
            )
        )
    ).all()
    return {"unknown_total": len(rows)}
