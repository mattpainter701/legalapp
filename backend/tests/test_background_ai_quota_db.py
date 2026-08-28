import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.platform import PlatformSetting
from app.models.tenant import Tenant
from app.services.background_ai_quota import (
    BACKGROUND_ROUTE_CONFIG_KEY,
    BackgroundOperationDuplicate,
    BackgroundQuotaExceeded,
    BackgroundQuotaLedger,
    BackgroundReservation,
    background_quota_snapshot,
)


def _tenant(tenant_id: uuid.UUID, domain: str) -> Tenant:
    return Tenant(
        id=tenant_id,
        name=f"Quota {domain}",
        domain=domain,
        billing_tier="payg",
        is_active=True,
    )


async def _configure(db, *, account: int = 1, tenant: int = 1) -> None:
    db.add(
        PlatformSetting(
            key=BACKGROUND_ROUTE_CONFIG_KEY,
            value={
                "quota": {
                    "account_five_hour": account,
                    "account_weekly": account,
                    "account_monthly": account,
                    "tenant_five_hour": tenant,
                    "tenant_weekly": tenant,
                    "tenant_monthly": tenant,
                    "reservation_ttl_minutes": 15,
                }
            },
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_concurrent_final_background_pool_slot_has_one_winner(
    test_engine, db_session
):
    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "concurrent.invalid"))
    await db_session.flush()
    await _configure(db_session, account=1, tenant=5)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    async def reserve(key: str):
        return await ledger.reserve(
            tenant_id=tenant_id,
            idempotency_key=key,
            request_id=str(uuid.uuid4()),
            surface="background_test",
            route_alias="clarity-background-test",
            estimated_micros=1_000,
        )

    outcomes = await asyncio.gather(
        reserve("operation-a"), reserve("operation-b"), return_exceptions=True
    )
    assert (
        len([item for item in outcomes if isinstance(item, BackgroundReservation)]) == 1
    )
    assert (
        len([item for item in outcomes if isinstance(item, BackgroundQuotaExceeded)])
        == 1
    )


@pytest.mark.asyncio
async def test_account_pool_limit_is_shared_across_tenants(test_engine, db_session):
    """The account cap is a pool-wide ledger, not one cap per firm."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [_tenant(tenant_a, "pool-a.invalid"), _tenant(tenant_b, "pool-b.invalid")]
    )
    await db_session.flush()
    await _configure(db_session, account=1, tenant=5)
    ledger = BackgroundQuotaLedger(
        session_factory=async_sessionmaker(test_engine, expire_on_commit=False)
    )

    first = await ledger.reserve(
        tenant_id=tenant_a,
        idempotency_key="pool-a-first",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background-test",
        estimated_micros=1_000,
    )
    with pytest.raises(BackgroundQuotaExceeded, match="account five-hour"):
        await ledger.reserve(
            tenant_id=tenant_b,
            idempotency_key="pool-b-first",
            request_id=str(uuid.uuid4()),
            surface="background_test",
            route_alias="clarity-background-test",
            estimated_micros=1_000,
        )
    assert first.tenant_id == tenant_a


@pytest.mark.asyncio
async def test_tenant_fairness_does_not_consume_another_firms_capacity(
    test_engine, db_session
):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [_tenant(tenant_a, "fair-a.invalid"), _tenant(tenant_b, "fair-b.invalid")]
    )
    await db_session.flush()
    await _configure(db_session, account=4, tenant=1)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    first = await ledger.reserve(
        tenant_id=tenant_a,
        idempotency_key="tenant-a-first",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background-test",
        estimated_micros=1_000,
    )
    with pytest.raises(BackgroundQuotaExceeded, match="tenant five-hour"):
        await ledger.reserve(
            tenant_id=tenant_a,
            idempotency_key="tenant-a-second",
            request_id=str(uuid.uuid4()),
            surface="background_test",
            route_alias="clarity-background-test",
            estimated_micros=1_000,
        )
    second_firm = await ledger.reserve(
        tenant_id=tenant_b,
        idempotency_key="tenant-b-first",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background-test",
        estimated_micros=1_000,
    )
    assert first.tenant_id == tenant_a
    assert second_firm.tenant_id == tenant_b


@pytest.mark.asyncio
async def test_release_restores_capacity_but_idempotency_stays_consumed(
    test_engine, db_session
):
    tenant_id = uuid.uuid4()
    db_session.add(_tenant(tenant_id, "release.invalid"))
    await db_session.flush()
    await _configure(db_session, account=1, tenant=1)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ledger = BackgroundQuotaLedger(session_factory=factory)

    reservation = await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key="released-operation",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background-test",
        estimated_micros=1_000,
    )
    await ledger.release(reservation, error_code="provider_rejected")
    with pytest.raises(BackgroundOperationDuplicate):
        await ledger.reserve(
            tenant_id=tenant_id,
            idempotency_key="released-operation",
            request_id=str(uuid.uuid4()),
            surface="background_test",
            route_alias="clarity-background-test",
            estimated_micros=1_000,
        )
    replacement = await ledger.reserve(
        tenant_id=tenant_id,
        idempotency_key="replacement-operation",
        request_id=str(uuid.uuid4()),
        surface="background_test",
        route_alias="clarity-background-test",
        estimated_micros=1_000,
    )
    await ledger.settle(
        replacement,
        provider_request_id="resp-1",
        tokens_in=9,
        tokens_out=4,
    )

    async with factory() as db:
        snapshot = await background_quota_snapshot(db)
    assert snapshot["five_hour"]["used"] == 1
    assert snapshot["weekly"]["used"] == 1
    assert snapshot["monthly"]["used"] == 1
    assert snapshot["surfaces"] == [{"surface": "background_test", "requests": 1}]
