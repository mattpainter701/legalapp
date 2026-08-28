"""The Background pool is metered in provider value, not request counts.

These cover the failure the request-count meter allowed: a handful of expensive
requests exhausting the real dollar window while the request counter still
reports thousands of calls of headroom.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.background_ai_usage import BackgroundAIUsageReservation
from app.models.platform import PlatformSetting
from app.models.tenant import Tenant
from app.services.ai_price_card import (
    MICROS_PER_USD,
    PRICE_CARD_SETTING_KEY,
    PriceCard,
    UnknownModelPrice,
    _coerce_rates,
    default_price_card,
    estimate_tokens_from_text,
    get_price_card,
    usd,
)
from app.services.background_ai_quota import (
    BACKGROUND_ROUTE_CONFIG_KEY,
    BackgroundQuotaExceeded,
    BackgroundQuotaLedger,
    background_quota_snapshot,
)
from app.services.background_ai_reconciliation import (
    ProviderOutcome,
    reconcile_unknown_reservations,
)


def _tenant(tenant_id: uuid.UUID, domain: str) -> Tenant:
    return Tenant(
        id=tenant_id,
        name=f"Value {domain}",
        domain=domain,
        billing_tier="payg",
        is_active=True,
    )


async def _configure_value_limits(
    db,
    *,
    account_usd: float,
    tenant_usd: float,
    requests: int = 1_000_000,
) -> None:
    """Set a tight dollar budget and a deliberately huge request ceiling."""

    micros = lambda amount: int(amount * MICROS_PER_USD)  # noqa: E731
    db.add(
        PlatformSetting(
            key=BACKGROUND_ROUTE_CONFIG_KEY,
            value={
                "quota": {
                    "account_five_hour": requests,
                    "account_weekly": requests,
                    "account_monthly": requests,
                    "tenant_five_hour": requests,
                    "tenant_weekly": requests,
                    "tenant_monthly": requests,
                    "reservation_ttl_minutes": 15,
                    "account_five_hour_micros": micros(account_usd),
                    "account_weekly_micros": micros(account_usd),
                    "account_monthly_micros": micros(account_usd),
                    "tenant_five_hour_micros": micros(tenant_usd),
                    "tenant_weekly_micros": micros(tenant_usd),
                    "tenant_monthly_micros": micros(tenant_usd),
                }
            },
        )
    )
    await db.commit()


# ── price card ────────────────────────────────────────────────────────────────


def test_price_card_prices_tokens_in_micros():
    card = PriceCard(version="t", rates={"m": {"input": 0.60, "output": 2.40}})
    # A rate is USD per million tokens: 4,000 in at $0.60/M plus 900 out at
    # $2.40/M is $0.0024 + $0.00216 = $0.00456.
    micros = card.estimate_max_micros(
        model="m", input_tokens=4_000, max_output_tokens=900
    )
    assert micros == 4_560
    # usd() is operator-facing and rounds to four places.
    assert usd(micros) == 0.0046


def test_price_card_estimate_rounds_up_never_down():
    card = PriceCard(version="t", rates={"m": {"input": 0.5, "output": 0.5}})
    # 1 token each side is 1.0 micros exactly; a fractional total must ceil so a
    # reservation can never hold less than the request can cost.
    assert card.estimate_max_micros(model="m", input_tokens=1, max_output_tokens=0) == 1
    assert card.estimate_max_micros(model="m", input_tokens=0, max_output_tokens=0) == 1


def test_price_card_resolves_revisioned_aliases_to_their_family():
    card = default_price_card()
    base = card.estimate_max_micros(
        model="clarity-background", input_tokens=100, max_output_tokens=100
    )
    revisioned = card.estimate_max_micros(
        model="clarity-background-r7", input_tokens=100, max_output_tokens=100
    )
    assert base == revisioned


def test_unknown_model_raises_rather_than_costing_nothing():
    card = default_price_card()
    with pytest.raises(UnknownModelPrice):
        card.estimate_max_micros(
            model="some-unpriced-model", input_tokens=10, max_output_tokens=10
        )


def test_empty_model_and_route_graph_fail_price_admission_closed():
    card = default_price_card()

    with pytest.raises(UnknownModelPrice):
        card.rate_for("")
    with pytest.raises(UnknownModelPrice):
        card.estimate_max_for_models(models=[], input_tokens=10, max_output_tokens=10)


def test_operator_price_overrides_accept_only_finite_positive_rates():
    rates = _coerce_rates(
        {
            123: {"input": 1, "output": 2},
            "not-a-rate": "invalid",
            "missing-output": {"input": 1},
            "zero-input": {"input": 0, "output": 2},
            "bad-cache": {"input": 1, "output": 2, "cached_read": "invalid"},
            "negative-cache": {"input": 1, "output": 2, "cached_read": -1},
            "valid": {
                "input": 1,
                "output": 2,
                "cached_read": 0.25,
                "threshold_tokens": 100,
                "input_over_threshold": 3,
                "output_over_threshold": 4,
            },
        }
    )

    assert rates == {
        "valid": {
            "input": 1.0,
            "output": 2.0,
            "cached_read": 0.25,
            "threshold_tokens": 100.0,
            "input_over_threshold": 3.0,
            "output_over_threshold": 4.0,
        }
    }


@pytest.mark.asyncio
async def test_price_card_database_failure_uses_safe_builtin_fallback():
    class FailingDB:
        async def scalar(self, _query):
            raise RuntimeError("database unavailable")

    card = await get_price_card(FailingDB())

    assert card.version == default_price_card().version
    assert card.has_rate("opencode-go/gpt-5.6-luna") is True


@pytest.mark.asyncio
async def test_operator_price_override_merges_with_builtin_card():
    class DB:
        async def scalar(self, _query):
            return PlatformSetting(
                key=PRICE_CARD_SETTING_KEY,
                value={
                    "version": "operator-test",
                    "rates": {"provider/custom": {"input": 1, "output": 2}},
                },
            )

    card = await get_price_card(DB())

    assert card.version == "operator-test"
    assert card.has_rate("provider/custom") is True
    assert card.has_rate("opencode-go/gpt-5.6-luna") is True


def test_route_reservation_prices_every_provider_target_and_uses_the_maximum():
    card = default_price_card()

    estimated, model = card.estimate_max_for_models(
        models=["opencode-go/gpt-5.6-luna", "opencode-go/kimi-k3"],
        input_tokens=100,
        max_output_tokens=100,
    )

    assert estimated == 1_800
    assert model == "opencode-go/kimi-k3"


def test_one_unpriced_fallback_fails_the_whole_route_closed():
    card = default_price_card()

    with pytest.raises(UnknownModelPrice, match="unpriced-fallback"):
        card.estimate_max_for_models(
            models=[
                "opencode-go/gpt-5.6-luna",
                "another-provider/unpriced-fallback",
            ],
            input_tokens=100,
            max_output_tokens=100,
        )


def test_long_context_tier_includes_the_possible_output_tokens():
    card = PriceCard(
        version="t",
        rates={
            "provider/model": {
                "input": 1.0,
                "output": 2.0,
                "threshold_tokens": 100,
                "input_over_threshold": 3.0,
                "output_over_threshold": 4.0,
            }
        },
    )

    # The prompt is below the threshold, but the full request context is not.
    assert (
        card.estimate_max_micros(
            model="provider/model", input_tokens=90, max_output_tokens=20
        )
        == 350
    )


def test_actual_price_accounts_for_cached_read_and_write_tokens():
    card = PriceCard(
        version="t",
        rates={
            "provider/model": {
                "input": 2.0,
                "output": 4.0,
                "cached_read": 0.5,
                "cached_write": 3.0,
            }
        },
    )

    assert (
        card.actual_micros(
            model="provider/model",
            tokens_in=100,
            tokens_out=10,
            cached_read_tokens=20,
            cached_write_tokens=30,
        )
        == 240
    )


def test_token_estimate_is_conservative():
    # Must round up: an underestimate here becomes real overspend.
    assert estimate_tokens_from_text("") == 0
    assert estimate_tokens_from_text("a") == 1
    assert estimate_tokens_from_text("a" * 35) >= 10


# ── value admission ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expensive_requests_exhaust_the_dollar_window_with_requests_to_spare(
    test_engine, db_session
):
    """The exact failure the request-count meter permitted."""

    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "value-burn.invalid"))
    await db_session.flush()
    # $1.00 of budget, but a million requests allowed.
    await _configure_value_limits(db_session, account_usd=1.00, tenant_usd=1.00)
    ledger = BackgroundQuotaLedger(
        session_factory=async_sessionmaker(test_engine, expire_on_commit=False)
    )

    # Four requests at $0.30 each fit; the fifth would exceed $1.00.
    thirty_cents = int(0.30 * MICROS_PER_USD)
    for index in range(3):
        await ledger.reserve(
            tenant_id=tenant_id,
            idempotency_key=f"burn-{index}",
            request_id=str(uuid.uuid4()),
            surface="background_test",
            route_alias="clarity-background",
            estimated_micros=thirty_cents,
        )

    with pytest.raises(BackgroundQuotaExceeded, match="spend"):
        await ledger.reserve(
            tenant_id=tenant_id,
            idempotency_key="burn-over",
            request_id=str(uuid.uuid4()),
            surface="background_test",
            route_alias="clarity-background",
            estimated_micros=thirty_cents,
        )


@pytest.mark.asyncio
async def test_admission_fits_the_whole_request_not_just_a_nonfull_window(
    test_engine, db_session
):
    """A window with $0.01 left must refuse a $0.50 request."""

    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "value-fit.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=1.00, tenant_usd=1.00)
    ledger = BackgroundQuotaLedger(
        session_factory=async_sessionmaker(test_engine, expire_on_commit=False)
    )

    await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key="fit-first",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background",
        estimated_micros=int(0.99 * MICROS_PER_USD),
    )
    with pytest.raises(BackgroundQuotaExceeded, match="spend"):
        await ledger.reserve(
            tenant_id=tenant_id,
            idempotency_key="fit-second",
            request_id=str(uuid.uuid4()),
            surface="background_test",
            route_alias="clarity-background",
            estimated_micros=int(0.50 * MICROS_PER_USD),
        )


@pytest.mark.asyncio
async def test_unpriced_reservation_is_refused(test_engine, db_session):
    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "value-unpriced.invalid"))
    await db_session.flush()
    ledger = BackgroundQuotaLedger(
        session_factory=async_sessionmaker(test_engine, expire_on_commit=False)
    )
    with pytest.raises(Exception, match="provider-value estimate"):
        await ledger.reserve(
            tenant_id=tenant_id,
            idempotency_key="unpriced",
            request_id=str(uuid.uuid4()),
            surface="background_test",
            route_alias="clarity-background",
            estimated_micros=0,
        )


@pytest.mark.asyncio
async def test_settlement_frees_the_difference_between_estimate_and_actual(
    test_engine, db_session
):
    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "value-settle.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=1.00, tenant_usd=1.00)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    reservation = await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key="settle-cheap",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background",
        estimated_micros=int(0.90 * MICROS_PER_USD),
    )
    # The request actually cost a cent, not ninety.
    await ledger.settle(
        reservation,
        provider_request_id="resp-cheap",
        tokens_in=10,
        tokens_out=10,
        actual_micros=int(0.01 * MICROS_PER_USD),
    )
    async with factory() as db:
        snapshot = await background_quota_snapshot(db)
    assert snapshot["value"]["monthly"]["spent_usd"] == 0.01

    # ...so the freed headroom is immediately reusable.
    await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key="settle-next",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background",
        estimated_micros=int(0.80 * MICROS_PER_USD),
    )


@pytest.mark.asyncio
async def test_unreported_usage_settles_at_the_estimate_not_zero(
    test_engine, db_session
):
    """A response with no usage numbers must never look free."""

    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "value-noreport.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=1.00, tenant_usd=1.00)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    reservation = await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key="noreport",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background",
        estimated_micros=int(0.40 * MICROS_PER_USD),
    )
    await ledger.settle(
        reservation, provider_request_id=None, tokens_in=0, tokens_out=0
    )
    async with factory() as db:
        snapshot = await background_quota_snapshot(db)
    assert snapshot["value"]["monthly"]["spent_usd"] == 0.40


@pytest.mark.asyncio
async def test_unknown_holds_its_estimate_and_release_does_not(test_engine, db_session):
    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "value-unknown.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=10.00, tenant_usd=10.00)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    ambiguous = await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key="ambiguous",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background",
        estimated_micros=int(0.25 * MICROS_PER_USD),
    )
    rejected = await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key="rejected",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background",
        estimated_micros=int(0.25 * MICROS_PER_USD),
    )
    await ledger.mark_unknown(ambiguous, error_code="ai_request_unknown")
    await ledger.release(rejected, error_code="provider_rejected")

    async with factory() as db:
        snapshot = await background_quota_snapshot(db)
    # Only the ambiguous one still holds budget.
    assert snapshot["value"]["monthly"]["spent_usd"] == 0.25
    assert snapshot["value"]["unreconciled"]["requests"] == 1
    assert snapshot["value"]["unreconciled"]["held_usd"] == 0.25


# ── reconciliation ────────────────────────────────────────────────────────────


async def _make_unknown(ledger, tenant_id, key, micros, provider_request_id="resp-x"):
    reservation = await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key=key,
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background",
        estimated_micros=micros,
    )
    await ledger.mark_unknown(reservation, error_code="ai_request_unknown")
    return reservation


async def _age_reservation(factory, reservation_id, *, minutes: int) -> None:
    """Backdate a row so the sweep's grace period has elapsed."""
    async with factory() as db:
        from app.services.background_ai_quota import _enable_background_quota_scope

        await _enable_background_quota_scope(db)
        row = await db.get(BackgroundAIUsageReservation, reservation_id)
        row.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        await db.commit()


@pytest.mark.asyncio
async def test_stale_reserved_request_becomes_unknown_without_losing_its_hold(
    test_engine, db_session
):
    """A worker crash after provider acceptance must not make spend disappear."""

    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "recon-stale-reserved.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=10.00, tenant_usd=10.00)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)
    reservation = await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key="stale-reserved",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background",
        estimated_micros=int(0.40 * MICROS_PER_USD),
    )
    await _age_reservation(factory, reservation.id, minutes=30)

    async def lookup(provider_request_id, route_alias):
        return None

    async with factory() as db:
        report = await reconcile_unknown_reservations(db, lookup=lookup)
    assert report.still_unknown == 1

    async with factory() as db:
        snapshot = await background_quota_snapshot(db)
        row = await db.scalar(
            select(BackgroundAIUsageReservation).where(
                BackgroundAIUsageReservation.id == reservation.id
            )
        )
    assert snapshot["value"]["monthly"]["spent_usd"] == 0.40
    assert snapshot["value"]["unreconciled"]["requests"] == 1
    assert row.status == "unknown"
    assert row.error_code == "reconcile_pending"


@pytest.mark.asyncio
async def test_reconciliation_settles_a_confirmed_billed_request(
    test_engine, db_session
):
    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "recon-billed.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=10.00, tenant_usd=10.00)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    reservation = await _make_unknown(
        ledger, tenant_id, "recon-billed", int(0.90 * MICROS_PER_USD)
    )
    await _age_reservation(factory, reservation.id, minutes=30)

    async def lookup(provider_request_id, route_alias):
        return ProviderOutcome(
            billed=True, tokens_in=100, tokens_out=100, model="clarity-background"
        )

    async with factory() as db:
        report = await reconcile_unknown_reservations(db, lookup=lookup)
    assert report.settled == 1

    async with factory() as db:
        snapshot = await background_quota_snapshot(db)
    # 100 in at $0.60/M + 100 out at $2.40/M = 300 micros, replacing $0.90.
    assert snapshot["value"]["monthly"]["spent_micros"] == 300
    assert snapshot["value"]["unreconciled"]["requests"] == 0


@pytest.mark.asyncio
async def test_reconciliation_releases_a_confirmed_unbilled_request(
    test_engine, db_session
):
    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "recon-free.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=10.00, tenant_usd=10.00)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    reservation = await _make_unknown(
        ledger, tenant_id, "recon-free", int(0.75 * MICROS_PER_USD)
    )
    await _age_reservation(factory, reservation.id, minutes=30)

    async def lookup(provider_request_id, route_alias):
        return ProviderOutcome(billed=False, not_billed=True)

    async with factory() as db:
        report = await reconcile_unknown_reservations(db, lookup=lookup)
    assert report.released == 1

    async with factory() as db:
        snapshot = await background_quota_snapshot(db)
    assert snapshot["value"]["monthly"]["spent_usd"] == 0.0


@pytest.mark.asyncio
async def test_a_failing_lookup_never_releases_capacity_it_cannot_verify(
    test_engine, db_session
):
    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "recon-raise.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=10.00, tenant_usd=10.00)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    reservation = await _make_unknown(
        ledger, tenant_id, "recon-raise", int(0.50 * MICROS_PER_USD)
    )
    await _age_reservation(factory, reservation.id, minutes=30)

    async def lookup(provider_request_id, route_alias):
        raise RuntimeError("gateway unreachable")

    async with factory() as db:
        report = await reconcile_unknown_reservations(db, lookup=lookup)
    assert report.released == 0
    assert report.settled == 0

    async with factory() as db:
        snapshot = await background_quota_snapshot(db)
    assert snapshot["value"]["monthly"]["spent_usd"] == 0.50


@pytest.mark.asyncio
async def test_unresolvable_reservations_age_out_but_keep_holding_their_estimate(
    test_engine, db_session, monkeypatch
):
    """Giving up must stop the retries, not forgive the spend."""

    import app.services.background_ai_reconciliation as recon

    monkeypatch.setattr(recon.settings, "BACKGROUND_AI_RECONCILE_MAX_AGE_HOURS", 1)
    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "recon-aged.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=10.00, tenant_usd=10.00)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    reservation = await _make_unknown(
        ledger, tenant_id, "recon-aged", int(0.60 * MICROS_PER_USD)
    )
    await _age_reservation(factory, reservation.id, minutes=180)

    async def lookup(provider_request_id, route_alias):
        return None  # never conclusive

    async with factory() as db:
        report = await reconcile_unknown_reservations(db, lookup=lookup)
    assert report.aged_out == 1

    async with factory() as db:
        snapshot = await background_quota_snapshot(db)
        # Spend is retained...
        assert snapshot["value"]["monthly"]["spent_usd"] == 0.60
        # ...and remains visible after retries stop, rather than disappearing
        # from the operator's accounting view.
        assert snapshot["value"]["unreconciled"]["requests"] == 1
        assert snapshot["value"]["unreconciled"]["aged_out_requests"] == 1
        assert snapshot["value"]["unreconciled"]["aged_out_held_usd"] == 0.60
        row = await db.scalar(
            select(BackgroundAIUsageReservation).where(
                BackgroundAIUsageReservation.id == reservation.id
            )
        )
        assert row.status == "unknown"
        assert row.reconciled_at is not None
        assert row.error_code == "reconcile_unresolved"

    # A second sweep must not pick it up again.
    async with factory() as db:
        again = await reconcile_unknown_reservations(db, lookup=lookup)
    assert again.scanned == 0


def test_reconciliation_is_registered_on_the_production_scheduler(monkeypatch):
    """The sweep must actually run somewhere, not only in tests.

    Without a registration, ambiguous reservations are never aged out and the
    operator panel's held-spend figure only ever grows.
    """

    from app.services import scheduler as scheduler_module

    registered = []

    class _FakeScheduler:
        running = False

        def add_job(self, _func, *_args, **kwargs):
            registered.append(kwargs.get("id"))

        def start(self):
            pass

    instance = scheduler_module.LegalScheduler()
    monkeypatch.setattr(instance, "scheduler", _FakeScheduler())
    instance.start()

    assert "background-ai-reconcile" in registered


@pytest.mark.asyncio
async def test_reconciliation_respects_the_grace_period(test_engine, db_session):
    """A request that may still be in flight is left alone."""

    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "recon-grace.invalid"))
    await db_session.flush()
    await _configure_value_limits(db_session, account_usd=10.00, tenant_usd=10.00)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    await _make_unknown(ledger, tenant_id, "recon-grace", int(0.10 * MICROS_PER_USD))

    async def lookup(provider_request_id, route_alias):
        raise AssertionError("must not be consulted inside the grace period")

    async with factory() as db:
        report = await reconcile_unknown_reservations(db, lookup=lookup)
    assert report.scanned == 0
