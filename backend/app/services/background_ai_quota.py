"""Atomic request reservations for the shared Background Automations pool."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker
from app.models.background_ai_usage import BackgroundAIUsageReservation
from app.models.platform import PlatformSetting
from app.services.ai_price_card import MICROS_PER_USD, usd


settings = get_settings()
BACKGROUND_ROUTE_CONFIG_KEY = "llm_background_route_v1"
COUNTED_STATUSES = ("settled", "unknown")
BACKGROUND_QUOTA_SCOPE_GUC = "app.background_ai_quota_scope"


async def _enable_background_quota_scope(db: AsyncSession) -> None:
    """Allow this transaction to read/write the shared quota ledger.

    This is deliberately separate from ``app.rls_bypass``: the migration's
    policy recognizes this selector only on the background quota table, and
    ``is_local=true`` makes it transaction-local.
    """

    await db.execute(
        text("SELECT set_config(:setting, 'on', true)"),
        {"setting": BACKGROUND_QUOTA_SCOPE_GUC},
    )


@asynccontextmanager
async def _background_quota_scope(db: AsyncSession):
    """Keep the cross-tenant selector enabled only for snapshot ledger reads."""

    await _enable_background_quota_scope(db)
    try:
        yield
    except BaseException:
        # A failed query may leave PostgreSQL's transaction aborted. Avoid
        # masking the original failure; closing/rolling back the transaction
        # clears this transaction-local selector.
        try:
            await db.execute(
                text("SELECT set_config(:setting, 'off', true)"),
                {"setting": BACKGROUND_QUOTA_SCOPE_GUC},
            )
        except Exception:
            pass
        raise
    else:
        # Snapshot callers may own a longer transaction, so explicitly turn
        # the table-specific selector off as soon as its reads are complete.
        await db.execute(
            text("SELECT set_config(:setting, 'off', true)"),
            {"setting": BACKGROUND_QUOTA_SCOPE_GUC},
        )


class BackgroundQuotaError(RuntimeError):
    code = "background_quota_error"


class BackgroundQuotaExceeded(BackgroundQuotaError):
    code = "background_quota_exceeded"

    def __init__(self, window: str) -> None:
        super().__init__(f"Background Automations quota exhausted for {window}")
        self.window = window


class BackgroundOperationDuplicate(BackgroundQuotaError):
    code = "background_operation_duplicate"


@dataclass(frozen=True)
class BackgroundQuotaLimits:
    """Both meters for the shared pool.

    The ``*_micros`` windows are authoritative: the provider bills value, so
    value is what admission has to respect. The request counts stay enforced as
    a coarse backstop and a fairness signal, but they are not the budget.
    """

    account_five_hour: int
    account_weekly: int
    account_monthly: int
    tenant_five_hour: int
    tenant_weekly: int
    tenant_monthly: int
    reservation_ttl_minutes: int
    account_five_hour_micros: int
    account_weekly_micros: int
    account_monthly_micros: int
    tenant_five_hour_micros: int
    tenant_weekly_micros: int
    tenant_monthly_micros: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class BackgroundReservation:
    id: uuid.UUID
    tenant_id: uuid.UUID
    request_id: str
    pool: str
    estimated_micros: int = 0
    price_card_version: str | None = None


def _usd_to_micros(value: Any, fallback_usd: float) -> int:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(fallback_usd)
    if parsed <= 0:
        parsed = float(fallback_usd)
    return max(1, int(round(parsed * MICROS_PER_USD)))


def default_background_quota_limits() -> BackgroundQuotaLimits:
    return BackgroundQuotaLimits(
        account_five_hour=max(1, settings.BACKGROUND_AI_ACCOUNT_FIVE_HOUR_LIMIT),
        account_weekly=max(1, settings.BACKGROUND_AI_ACCOUNT_WEEKLY_LIMIT),
        account_monthly=max(1, settings.BACKGROUND_AI_ACCOUNT_MONTHLY_LIMIT),
        tenant_five_hour=max(1, settings.BACKGROUND_AI_TENANT_FIVE_HOUR_LIMIT),
        tenant_weekly=max(1, settings.BACKGROUND_AI_TENANT_WEEKLY_LIMIT),
        tenant_monthly=max(1, settings.BACKGROUND_AI_TENANT_MONTHLY_LIMIT),
        reservation_ttl_minutes=max(1, settings.BACKGROUND_AI_RESERVATION_TTL_MINUTES),
        account_five_hour_micros=_usd_to_micros(
            settings.BACKGROUND_AI_ACCOUNT_FIVE_HOUR_USD, 12.0
        ),
        account_weekly_micros=_usd_to_micros(
            settings.BACKGROUND_AI_ACCOUNT_WEEKLY_USD, 30.0
        ),
        account_monthly_micros=_usd_to_micros(
            settings.BACKGROUND_AI_ACCOUNT_MONTHLY_USD, 60.0
        ),
        tenant_five_hour_micros=_usd_to_micros(
            settings.BACKGROUND_AI_TENANT_FIVE_HOUR_USD, 3.0
        ),
        tenant_weekly_micros=_usd_to_micros(
            settings.BACKGROUND_AI_TENANT_WEEKLY_USD, 8.0
        ),
        tenant_monthly_micros=_usd_to_micros(
            settings.BACKGROUND_AI_TENANT_MONTHLY_USD, 15.0
        ),
    )


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


async def get_background_quota_limits(
    db: AsyncSession,
) -> BackgroundQuotaLimits:
    defaults = default_background_quota_limits()
    row = await db.scalar(
        select(PlatformSetting).where(
            PlatformSetting.key == BACKGROUND_ROUTE_CONFIG_KEY
        )
    )
    value = row.value if row and isinstance(row.value, dict) else {}
    configured = value.get("quota") if isinstance(value.get("quota"), dict) else {}
    return BackgroundQuotaLimits(
        account_five_hour=_positive_int(
            configured.get("account_five_hour"), defaults.account_five_hour
        ),
        account_weekly=_positive_int(
            configured.get("account_weekly"), defaults.account_weekly
        ),
        account_monthly=_positive_int(
            configured.get("account_monthly"), defaults.account_monthly
        ),
        tenant_five_hour=_positive_int(
            configured.get("tenant_five_hour"), defaults.tenant_five_hour
        ),
        tenant_weekly=_positive_int(
            configured.get("tenant_weekly"), defaults.tenant_weekly
        ),
        tenant_monthly=_positive_int(
            configured.get("tenant_monthly"), defaults.tenant_monthly
        ),
        reservation_ttl_minutes=_positive_int(
            configured.get("reservation_ttl_minutes"),
            defaults.reservation_ttl_minutes,
        ),
        account_five_hour_micros=_positive_int(
            configured.get("account_five_hour_micros"),
            defaults.account_five_hour_micros,
        ),
        account_weekly_micros=_positive_int(
            configured.get("account_weekly_micros"), defaults.account_weekly_micros
        ),
        account_monthly_micros=_positive_int(
            configured.get("account_monthly_micros"), defaults.account_monthly_micros
        ),
        tenant_five_hour_micros=_positive_int(
            configured.get("tenant_five_hour_micros"),
            defaults.tenant_five_hour_micros,
        ),
        tenant_weekly_micros=_positive_int(
            configured.get("tenant_weekly_micros"), defaults.tenant_weekly_micros
        ),
        tenant_monthly_micros=_positive_int(
            configured.get("tenant_monthly_micros"), defaults.tenant_monthly_micros
        ),
    )


def _month_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _counted_predicate(now: datetime, limits: BackgroundQuotaLimits):
    active_reservation_cutoff = now - timedelta(minutes=limits.reservation_ttl_minutes)
    return or_(
        BackgroundAIUsageReservation.status.in_(COUNTED_STATUSES),
        and_(
            BackgroundAIUsageReservation.status == "reserved",
            BackgroundAIUsageReservation.created_at >= active_reservation_cutoff,
        ),
    )


async def _usage_count(
    db: AsyncSession,
    *,
    pool: str,
    since: datetime,
    counted_predicate,
    tenant_id: uuid.UUID | None = None,
) -> int:
    query = select(func.count(BackgroundAIUsageReservation.id)).where(
        BackgroundAIUsageReservation.pool == pool,
        BackgroundAIUsageReservation.created_at >= since,
        counted_predicate,
    )
    if tenant_id is not None:
        query = query.where(BackgroundAIUsageReservation.tenant_id == tenant_id)
    return int(await db.scalar(query) or 0)


def _spend_expression():
    """Provider value a reservation currently holds against the budget.

    A settled row costs what the provider actually reported. An in-flight or
    ambiguous row holds its worst-case estimate, because assuming zero for work
    that may well have been billed is how a pool silently overspends. A released
    row cost nothing — that status is only set when the provider rejected the
    request before doing any work.
    """

    return case(
        (
            BackgroundAIUsageReservation.status == "settled",
            BackgroundAIUsageReservation.actual_micros,
        ),
        (
            BackgroundAIUsageReservation.status.in_(("reserved", "unknown")),
            BackgroundAIUsageReservation.estimated_micros,
        ),
        else_=0,
    )


async def _usage_micros(
    db: AsyncSession,
    *,
    pool: str,
    since: datetime,
    counted_predicate,
    tenant_id: uuid.UUID | None = None,
) -> int:
    query = select(func.coalesce(func.sum(_spend_expression()), 0)).where(
        BackgroundAIUsageReservation.pool == pool,
        BackgroundAIUsageReservation.created_at >= since,
        counted_predicate,
    )
    if tenant_id is not None:
        query = query.where(BackgroundAIUsageReservation.tenant_id == tenant_id)
    return int(await db.scalar(query) or 0)


class BackgroundQuotaLedger:
    """Reserve globally and per tenant before making a provider request.

    A PostgreSQL transaction advisory lock serializes the short admission-control
    section across web workers. External inference never runs under that lock.
    """

    def __init__(self, *, session_factory=async_session_maker) -> None:
        self.session_factory = session_factory

    async def reserve(
        self,
        *,
        tenant_id: uuid.UUID,
        idempotency_key: str,
        request_id: str,
        surface: str,
        route_alias: str,
        estimated_micros: int,
        price_card_version: str | None = None,
        pool: str | None = None,
    ) -> BackgroundReservation:
        selected_pool = (pool or settings.BACKGROUND_AI_POOL).strip()
        if not selected_pool:
            raise BackgroundQuotaError("Background pool is not configured")
        if not idempotency_key or len(idempotency_key) > 200:
            raise BackgroundQuotaError("A bounded idempotency key is required")
        # An unpriced request is not a free request. The caller must price the
        # work before it can hold pool capacity.
        estimate = int(estimated_micros)
        if estimate <= 0:
            raise BackgroundQuotaError(
                "A positive provider-value estimate is required to reserve capacity"
            )

        now = datetime.now(timezone.utc)
        async with self.session_factory() as db:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"background-ai-quota:{selected_pool}"},
            )
            limits = await get_background_quota_limits(db)
            # The stale-row sweep, duplicate check, and account-wide counts
            # intentionally cross tenant boundaries. RLS permits that access
            # only through this transaction-local, table-specific selector.
            await _enable_background_quota_scope(db)
            stale_cutoff = now - timedelta(minutes=limits.reservation_ttl_minutes)
            await db.execute(
                update(BackgroundAIUsageReservation)
                .where(
                    BackgroundAIUsageReservation.pool == selected_pool,
                    BackgroundAIUsageReservation.status == "reserved",
                    BackgroundAIUsageReservation.created_at < stale_cutoff,
                )
                .values(
                    status="released",
                    settled_at=now,
                    error_code="reservation_expired",
                )
            )
            existing = await db.scalar(
                select(BackgroundAIUsageReservation.id).where(
                    BackgroundAIUsageReservation.pool == selected_pool,
                    BackgroundAIUsageReservation.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                await db.rollback()
                raise BackgroundOperationDuplicate(
                    "Background operation was already submitted"
                )

            counted = _counted_predicate(now, limits)
            windows = (
                (
                    "account five-hour",
                    now - timedelta(hours=5),
                    None,
                    limits.account_five_hour,
                ),
                (
                    "account weekly",
                    now - timedelta(days=7),
                    None,
                    limits.account_weekly,
                ),
                (
                    "account monthly",
                    _month_start(now),
                    None,
                    limits.account_monthly,
                ),
                (
                    "tenant five-hour",
                    now - timedelta(hours=5),
                    tenant_id,
                    limits.tenant_five_hour,
                ),
                (
                    "tenant weekly",
                    now - timedelta(days=7),
                    tenant_id,
                    limits.tenant_weekly,
                ),
                (
                    "tenant monthly",
                    _month_start(now),
                    tenant_id,
                    limits.tenant_monthly,
                ),
            )
            # Provider value is the real budget, so it is checked first and its
            # exhaustion is the one reported when both meters would refuse.
            value_windows = (
                (
                    "account five-hour spend",
                    now - timedelta(hours=5),
                    None,
                    limits.account_five_hour_micros,
                ),
                (
                    "account weekly spend",
                    now - timedelta(days=7),
                    None,
                    limits.account_weekly_micros,
                ),
                (
                    "account monthly spend",
                    _month_start(now),
                    None,
                    limits.account_monthly_micros,
                ),
                (
                    "tenant five-hour spend",
                    now - timedelta(hours=5),
                    tenant_id,
                    limits.tenant_five_hour_micros,
                ),
                (
                    "tenant weekly spend",
                    now - timedelta(days=7),
                    tenant_id,
                    limits.tenant_weekly_micros,
                ),
                (
                    "tenant monthly spend",
                    _month_start(now),
                    tenant_id,
                    limits.tenant_monthly_micros,
                ),
            )
            for window, since, scoped_tenant_id, limit_micros in value_windows:
                spent = await _usage_micros(
                    db,
                    pool=selected_pool,
                    since=since,
                    counted_predicate=counted,
                    tenant_id=scoped_tenant_id,
                )
                # Admission must fit this request's worst case inside the
                # window, not merely find the window not yet full.
                if spent + estimate > limit_micros:
                    await db.rollback()
                    raise BackgroundQuotaExceeded(window)

            for window, since, scoped_tenant_id, limit in windows:
                used = await _usage_count(
                    db,
                    pool=selected_pool,
                    since=since,
                    counted_predicate=counted,
                    tenant_id=scoped_tenant_id,
                )
                if used >= limit:
                    await db.rollback()
                    raise BackgroundQuotaExceeded(window)

            row = BackgroundAIUsageReservation(
                tenant_id=tenant_id,
                pool=selected_pool,
                idempotency_key=idempotency_key,
                request_id=request_id,
                surface=surface[:80],
                route_alias=route_alias[:200],
                status="reserved",
                estimated_micros=estimate,
                price_card_version=(price_card_version or None),
            )
            db.add(row)
            await db.flush()
            reservation = BackgroundReservation(
                id=row.id,
                tenant_id=tenant_id,
                request_id=request_id,
                pool=selected_pool,
                estimated_micros=estimate,
                price_card_version=price_card_version,
            )
            await db.commit()
            return reservation

    async def _finish(
        self,
        reservation: BackgroundReservation,
        *,
        status: str,
        provider_request_id: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        actual_micros: int = 0,
        error_code: str | None = None,
    ) -> None:
        async with self.session_factory() as db:
            # Settlement/release/unknown updates identify a reservation by ID
            # and may run outside the originating tenant request context.
            # Enable only the dedicated transaction-local quota selector.
            await _enable_background_quota_scope(db)
            await db.execute(
                update(BackgroundAIUsageReservation)
                .where(
                    BackgroundAIUsageReservation.id == reservation.id,
                    BackgroundAIUsageReservation.status == "reserved",
                )
                .values(
                    status=status,
                    provider_request_id=(provider_request_id or None),
                    tokens_in=max(0, int(tokens_in)),
                    tokens_out=max(0, int(tokens_out)),
                    actual_micros=max(0, int(actual_micros)),
                    error_code=error_code,
                    settled_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()

    async def settle(
        self,
        reservation: BackgroundReservation,
        *,
        provider_request_id: str | None,
        tokens_in: int,
        tokens_out: int,
        actual_micros: int | None = None,
    ) -> None:
        # Without a priced settlement the reservation keeps holding its
        # estimate, so an unpriced response can never look cheaper than it was.
        settled_micros = (
            int(actual_micros)
            if actual_micros is not None
            else int(reservation.estimated_micros)
        )
        await self._finish(
            reservation,
            status="settled",
            provider_request_id=provider_request_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            actual_micros=settled_micros,
        )

    async def mark_unknown(
        self, reservation: BackgroundReservation, *, error_code: str
    ) -> None:
        await self._finish(
            reservation,
            status="unknown",
            error_code=error_code,
        )

    async def release(
        self, reservation: BackgroundReservation, *, error_code: str
    ) -> None:
        await self._finish(
            reservation,
            status="released",
            error_code=error_code,
        )


async def background_quota_snapshot(
    db: AsyncSession, *, pool: str | None = None
) -> dict[str, Any]:
    """Return content-free operator metrics for burn and fairness monitoring."""

    selected_pool = (pool or settings.BACKGROUND_AI_POOL).strip()
    now = datetime.now(timezone.utc)
    limits = await get_background_quota_limits(db)
    counted = _counted_predicate(now, limits)
    # Operator snapshots intentionally aggregate all tenants, but the scope
    # is cleared immediately after the ledger reads for caller-owned sessions.
    async with _background_quota_scope(db):
        five_hour_used = await _usage_count(
            db,
            pool=selected_pool,
            since=now - timedelta(hours=5),
            counted_predicate=counted,
        )
        weekly_used = await _usage_count(
            db,
            pool=selected_pool,
            since=now - timedelta(days=7),
            counted_predicate=counted,
        )
        monthly_used = await _usage_count(
            db,
            pool=selected_pool,
            since=_month_start(now),
            counted_predicate=counted,
        )
        five_hour_micros = await _usage_micros(
            db,
            pool=selected_pool,
            since=now - timedelta(hours=5),
            counted_predicate=counted,
        )
        weekly_micros = await _usage_micros(
            db,
            pool=selected_pool,
            since=now - timedelta(days=7),
            counted_predicate=counted,
        )
        monthly_micros = await _usage_micros(
            db,
            pool=selected_pool,
            since=_month_start(now),
            counted_predicate=counted,
        )
        # Ambiguous reservations still holding capacity. A rising number here is
        # the operator's signal that reconciliation is falling behind or that a
        # provider is timing out after accepting work.
        unreconciled = await db.scalar(
            select(
                func.count(BackgroundAIUsageReservation.id),
            ).where(
                BackgroundAIUsageReservation.pool == selected_pool,
                BackgroundAIUsageReservation.status == "unknown",
                BackgroundAIUsageReservation.reconciled_at.is_(None),
            )
        )
        unreconciled_micros = await db.scalar(
            select(
                func.coalesce(
                    func.sum(BackgroundAIUsageReservation.estimated_micros), 0
                )
            ).where(
                BackgroundAIUsageReservation.pool == selected_pool,
                BackgroundAIUsageReservation.status == "unknown",
                BackgroundAIUsageReservation.reconciled_at.is_(None),
            )
        )
        tenant_rows = (
            await db.execute(
                select(
                    BackgroundAIUsageReservation.tenant_id,
                    func.count(BackgroundAIUsageReservation.id).label("requests"),
                )
                .where(
                    BackgroundAIUsageReservation.pool == selected_pool,
                    BackgroundAIUsageReservation.created_at >= _month_start(now),
                    counted,
                )
                .group_by(BackgroundAIUsageReservation.tenant_id)
                .order_by(func.count(BackgroundAIUsageReservation.id).desc())
                .limit(20)
            )
        ).all()
        surface_rows = (
            await db.execute(
                select(
                    BackgroundAIUsageReservation.surface,
                    func.count(BackgroundAIUsageReservation.id).label("requests"),
                )
                .where(
                    BackgroundAIUsageReservation.pool == selected_pool,
                    BackgroundAIUsageReservation.created_at >= _month_start(now),
                    counted,
                )
                .group_by(BackgroundAIUsageReservation.surface)
                .order_by(func.count(BackgroundAIUsageReservation.id).desc())
            )
        ).all()
    days_in_month = (
        (_month_start(now) + timedelta(days=32)).replace(day=1) - _month_start(now)
    ).days
    elapsed = now - _month_start(now)
    month_elapsed_percent = min(
        100.0,
        100.0 * elapsed.total_seconds() / (days_in_month * 86400),
    )

    def _value_window(spent: int, limit_micros: int) -> dict[str, Any]:
        return {
            "spent_usd": usd(spent),
            "limit_usd": usd(limit_micros),
            "remaining_usd": usd(max(0, limit_micros - spent)),
            "spent_micros": spent,
            "limit_micros": limit_micros,
            "percent": round(100 * spent / limit_micros, 2) if limit_micros else 0.0,
        }

    monthly_value = _value_window(monthly_micros, limits.account_monthly_micros)
    # Straight-line projection of this month's spend to the reset date.
    projected_month_micros = int(
        monthly_micros / max(month_elapsed_percent / 100, 0.01)
    )
    monthly_value.update(
        {
            "month_elapsed_percent": round(month_elapsed_percent, 2),
            "projected_month_usd": usd(projected_month_micros),
            "projected_over_budget": projected_month_micros
            > limits.account_monthly_micros,
        }
    )

    return {
        "pool": selected_pool,
        "limits": limits.as_dict(),
        # Provider value is the enforced budget.
        "value": {
            "five_hour": _value_window(
                five_hour_micros, limits.account_five_hour_micros
            ),
            "weekly": _value_window(weekly_micros, limits.account_weekly_micros),
            "monthly": monthly_value,
            "unreconciled": {
                "requests": int(unreconciled or 0),
                "held_usd": usd(int(unreconciled_micros or 0)),
            },
        },
        # Request counts remain a coarse backstop and a planning metric. They
        # are not the provider's limit and must not be read as one.
        "five_hour": {
            "used": five_hour_used,
            "remaining": max(0, limits.account_five_hour - five_hour_used),
            "percent": round(100 * five_hour_used / limits.account_five_hour, 2),
        },
        "weekly": {
            "used": weekly_used,
            "remaining": max(0, limits.account_weekly - weekly_used),
            "percent": round(100 * weekly_used / limits.account_weekly, 2),
        },
        "monthly": {
            "used": monthly_used,
            "remaining": max(0, limits.account_monthly - monthly_used),
            "percent": round(100 * monthly_used / limits.account_monthly, 2),
            "month_elapsed_percent": round(month_elapsed_percent, 2),
            "projected_over_budget": (
                monthly_used / max(month_elapsed_percent / 100, 0.01)
                > limits.account_monthly
            ),
        },
        "tenants": [
            {"tenant_id": str(tenant_id), "requests": int(requests)}
            for tenant_id, requests in tenant_rows
        ],
        "surfaces": [
            {"surface": surface, "requests": int(requests)}
            for surface, requests in surface_rows
        ],
    }
