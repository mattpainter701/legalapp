"""Atomic request reservations for the shared Background Automations pool."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_maker
from app.models.background_ai_usage import BackgroundAIUsageReservation
from app.models.platform import PlatformSetting


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
    account_five_hour: int
    account_weekly: int
    account_monthly: int
    tenant_five_hour: int
    tenant_weekly: int
    tenant_monthly: int
    reservation_ttl_minutes: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class BackgroundReservation:
    id: uuid.UUID
    tenant_id: uuid.UUID
    request_id: str
    pool: str


def default_background_quota_limits() -> BackgroundQuotaLimits:
    return BackgroundQuotaLimits(
        account_five_hour=max(1, settings.BACKGROUND_AI_ACCOUNT_FIVE_HOUR_LIMIT),
        account_weekly=max(1, settings.BACKGROUND_AI_ACCOUNT_WEEKLY_LIMIT),
        account_monthly=max(1, settings.BACKGROUND_AI_ACCOUNT_MONTHLY_LIMIT),
        tenant_five_hour=max(1, settings.BACKGROUND_AI_TENANT_FIVE_HOUR_LIMIT),
        tenant_weekly=max(1, settings.BACKGROUND_AI_TENANT_WEEKLY_LIMIT),
        tenant_monthly=max(1, settings.BACKGROUND_AI_TENANT_MONTHLY_LIMIT),
        reservation_ttl_minutes=max(1, settings.BACKGROUND_AI_RESERVATION_TTL_MINUTES),
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
        pool: str | None = None,
    ) -> BackgroundReservation:
        selected_pool = (pool or settings.BACKGROUND_AI_POOL).strip()
        if not selected_pool:
            raise BackgroundQuotaError("Background pool is not configured")
        if not idempotency_key or len(idempotency_key) > 200:
            raise BackgroundQuotaError("A bounded idempotency key is required")

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
            )
            db.add(row)
            await db.flush()
            reservation = BackgroundReservation(
                id=row.id,
                tenant_id=tenant_id,
                request_id=request_id,
                pool=selected_pool,
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
    ) -> None:
        await self._finish(
            reservation,
            status="settled",
            provider_request_id=provider_request_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
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
    return {
        "pool": selected_pool,
        "limits": limits.as_dict(),
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
