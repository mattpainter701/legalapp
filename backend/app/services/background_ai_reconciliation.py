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

import asyncio
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import asyncpg
import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker
from app.models.background_ai_usage import BackgroundAIUsageReservation
from app.services.ai_price_card import MICROS_PER_USD, UnknownModelPrice, get_price_card
from app.services.background_ai_quota import (
    _background_quota_scope,
    get_background_quota_limits,
)


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
    #: Authoritative gateway spend, including cache/tier/provider adjustments.
    actual_micros: int | None = None

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
    """Look up one request in LiteLLM's metadata-only spend ledger.

    The proxy API is the normal path. The database fallback is intentional:
    some LiteLLM endpoint/version combinations replace ``request_id`` while
    retaining our correlation id in metadata. LegalApp already receives this
    database URL for metadata-only spend reporting, so the fallback can match
    either representation without enabling raw prompt logging.
    """

    del route_alias  # The spend row is authoritative; alias is audit context.
    if not correlation_id:
        return None
    api_error: Exception | None = None
    if settings.LITELLM_API_KEY:
        try:
            outcome = await _lookup_spend_api(correlation_id)
        except (httpx.HTTPError, ValueError) as exc:
            api_error = exc
        else:
            if outcome is not None:
                return outcome
    if settings.LITELLM_DATABASE_URL:
        try:
            return await _lookup_spend_database(correlation_id)
        except (asyncpg.PostgresError, OSError, ValueError):
            logger.warning(
                "background_ai.reconcile_database_lookup_failed", exc_info=True
            )
    if api_error is not None:
        raise api_error
    return None


async def _lookup_spend_api(correlation_id: str) -> ProviderOutcome | None:
    """Query LiteLLM's supported spend-log API by request id."""

    base = settings.LITELLM_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    timeout = max(
        1.0,
        min(30.0, float(settings.BACKGROUND_AI_RECONCILE_LOOKUP_TIMEOUT_SECONDS)),
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            f"{base}/spend/logs",
            params={"request_id": correlation_id, "summarize": "false"},
            headers={"Authorization": f"Bearer {settings.LITELLM_API_KEY}"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        candidate = payload.get("data") or payload.get("model") or payload.get("logs")
        rows = candidate if isinstance(candidate, list) else [payload]
    else:
        return None
    row = next(
        (
            item
            for item in rows
            if isinstance(item, dict)
            and str(item.get("request_id") or "") == correlation_id
        ),
        None,
    )
    return _outcome_from_spend_row(row) if row is not None else None


async def _lookup_spend_database(correlation_id: str) -> ProviderOutcome | None:
    """Match a LiteLLM spend row by native id or protected request metadata."""

    database_url = settings.LITELLM_DATABASE_URL.replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    timeout = max(
        1.0,
        min(30.0, float(settings.BACKGROUND_AI_RECONCILE_LOOKUP_TIMEOUT_SECONDS)),
    )
    connection = await asyncpg.connect(database_url, timeout=timeout)
    try:
        row = await connection.fetchrow(
            """
            SELECT "request_id", "spend", "prompt_tokens",
                   "completion_tokens", "model"
            FROM "LiteLLM_SpendLogs"
            WHERE "request_id" = $1
               OR "metadata" ->> 'request_id' = $1
               OR "metadata" -> 'spend_logs_metadata' ->> 'request_id' = $1
            ORDER BY "endTime" DESC NULLS LAST
            LIMIT 1
            """,
            correlation_id,
        )
    finally:
        await connection.close()
    return _outcome_from_spend_row(dict(row)) if row is not None else None


def _outcome_from_spend_row(row: dict[str, Any]) -> ProviderOutcome | None:
    """Convert the common API/database spend-row shape without guessing free."""

    tokens_in = max(0, int(row.get("prompt_tokens") or 0))
    tokens_out = max(0, int(row.get("completion_tokens") or 0))
    try:
        spend = Decimal(str(row.get("spend") or "0"))
    except (InvalidOperation, ValueError):
        spend = Decimal(0)
    if not spend.is_finite() or spend < 0:
        return None
    if spend == 0:
        if not (tokens_in or tokens_out):
            # Presence of a zero row is not proof that the upstream provider did
            # no work; keep the estimate unless it gives an explicit answer.
            return ProviderOutcome.inconclusive()
        # LiteLLM occasionally records a transient zero before cost calculation
        # catches up. Usage proves work happened, but not its final provider
        # price, so retry the ledger instead of settling it as free.
        return ProviderOutcome.inconclusive()
    actual_micros = int(
        (spend * MICROS_PER_USD).to_integral_value(rounding=ROUND_CEILING)
    )
    return ProviderOutcome(
        billed=True,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model=str(row.get("model") or "") or None,
        actual_micros=actual_micros,
    )


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
        limits = await get_background_quota_limits(session)
        reservation_cutoff = moment - timedelta(minutes=limits.reservation_ttl_minutes)
        async with _background_quota_scope(session):
            rows = (
                (
                    await session.execute(
                        select(BackgroundAIUsageReservation)
                        .where(
                            or_(
                                and_(
                                    BackgroundAIUsageReservation.status == "unknown",
                                    BackgroundAIUsageReservation.reconciled_at.is_(
                                        None
                                    ),
                                    # Give an in-flight provider a chance to
                                    # finish before treating silence as an answer.
                                    BackgroundAIUsageReservation.created_at
                                    <= moment - grace,
                                ),
                                and_(
                                    BackgroundAIUsageReservation.status == "reserved",
                                    BackgroundAIUsageReservation.created_at
                                    <= reservation_cutoff,
                                ),
                            )
                        )
                        .order_by(BackgroundAIUsageReservation.created_at)
                        .limit(batch)
                    )
                )
                .scalars()
                .all()
            )
        # End the selector/read transaction before any network I/O. Detaching
        # preserves the scalar audit fields even if a caller uses an
        # expire-on-commit session.
        for row in rows:
            session.expunge(row)
        await session.commit()
        if not rows:
            return report

        semaphore = asyncio.Semaphore(min(10, batch))

        async def _resolve_row(
            row: BackgroundAIUsageReservation,
        ) -> tuple[BackgroundAIUsageReservation, ProviderOutcome | None]:
            outcome: ProviderOutcome | None = None
            correlation_ids = list(
                dict.fromkeys(
                    value
                    for value in (row.request_id, row.provider_request_id)
                    if value
                )
            )
            try:
                async with semaphore:
                    for correlation_id in correlation_ids:
                        outcome = await resolve(correlation_id, row.route_alias or "")
                        if outcome is not None:
                            break
            except Exception:
                logger.warning(
                    "background_ai.reconcile_lookup_failed",
                    extra={"reservation_id": str(row.id)},
                    exc_info=True,
                )
            return row, outcome

        report.scanned = len(rows)
        outcomes = await asyncio.gather(*(_resolve_row(row) for row in rows))

        price_card = await get_price_card(session)
        async with _background_quota_scope(session):
            for row, outcome in outcomes:
                aged_out = (
                    row.created_at is not None and row.created_at <= moment - max_age
                )

                if outcome is not None and outcome.billed:
                    error_code = "reconciled_billed"
                    actual = outcome.actual_micros
                    if actual is None:
                        try:
                            actual = price_card.actual_micros(
                                model=outcome.model or row.pricing_model or "",
                                tokens_in=outcome.tokens_in,
                                tokens_out=outcome.tokens_out,
                            )
                        except UnknownModelPrice:
                            # Billed is conclusive but its exact price is not.
                            # Settle at the already-conservative estimate.
                            actual = row.estimated_micros
                            error_code = "reconciled_billed_cost_unknown"
                    await _apply(
                        session,
                        row.id,
                        status="settled",
                        tokens_in=outcome.tokens_in,
                        tokens_out=outcome.tokens_out,
                        actual_micros=actual,
                        error_code=error_code,
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
                    # Stop re-asking but retain both the spend and a distinct
                    # operator-visible aged-out count.
                    await session.execute(
                        update(BackgroundAIUsageReservation)
                        .where(
                            BackgroundAIUsageReservation.id == row.id,
                            BackgroundAIUsageReservation.status.in_(
                                ("unknown", "reserved")
                            ),
                        )
                        .values(
                            status="unknown",
                            reconciled_at=moment,
                            reconcile_attempts=(row.reconcile_attempts or 0) + 1,
                            error_code="reconcile_unresolved",
                        )
                    )
                    report.aged_out += 1
                else:
                    was_reserved = row.status == "reserved"
                    await session.execute(
                        update(BackgroundAIUsageReservation)
                        .where(
                            BackgroundAIUsageReservation.id == row.id,
                            BackgroundAIUsageReservation.status.in_(
                                ("unknown", "reserved")
                            ),
                        )
                        .values(
                            status="unknown",
                            reconcile_attempts=(row.reconcile_attempts or 0) + 1,
                            error_code=(
                                "reconcile_pending" if was_reserved else row.error_code
                            ),
                        )
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
            BackgroundAIUsageReservation.status.in_(("unknown", "reserved")),
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

    async with _background_quota_scope(db):
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
