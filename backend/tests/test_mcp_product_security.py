import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import mcp
from app.models.mcp_product import MCPProductKey, MCPUsageEvent
from app.services import mcp_product


def _tenant(**overrides):
    values = {
        "is_active": True,
        "mcp_entitlement_status": "enabled",
        "mcp_billing_status": "active",
        "stripe_customer_id": "cus_test",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _enable_product(monkeypatch):
    monkeypatch.setattr(mcp_product.settings, "MCP_PRODUCT_ENABLED", True)
    monkeypatch.setattr(mcp_product.settings, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(
        mcp_product.settings, "STRIPE_MCP_METER_EVENT_NAME", "mcp_calls"
    )


def test_product_access_is_globally_fail_closed(monkeypatch):
    monkeypatch.setattr(mcp_product.settings, "MCP_PRODUCT_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        mcp_product.ensure_mcp_product_access(_tenant())
    assert exc.value.status_code == 503


@pytest.mark.parametrize(
    ("overrides", "status_code"),
    [
        ({"is_active": False}, 403),
        ({"mcp_entitlement_status": "disabled"}, 403),
        ({"mcp_billing_status": "past_due"}, 402),
        ({"stripe_customer_id": None}, 402),
    ],
)
def test_product_access_rechecks_tenant_and_billing(
    monkeypatch, overrides, status_code
):
    _enable_product(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        mcp_product.ensure_mcp_product_access(_tenant(**overrides))
    assert exc.value.status_code == status_code


@pytest.mark.asyncio
async def test_per_key_burst_limit_returns_retry_after(monkeypatch):
    class FakeRedis:
        calls = 0

        async def eval(self, *args):
            self.calls += 1
            return [self.calls, 37]

    key = SimpleNamespace(id=uuid.uuid4(), burst_limit_per_minute=2)
    redis = FakeRedis()
    await mcp_product.enforce_product_key_burst_limit(redis, key)
    await mcp_product.enforce_product_key_burst_limit(redis, key)
    with pytest.raises(HTTPException) as exc:
        await mcp_product.enforce_product_key_burst_limit(redis, key)
    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "37"}


@pytest.mark.asyncio
async def test_rate_limiter_fails_closed_without_redis_in_production(monkeypatch):
    monkeypatch.setattr(mcp_product.settings, "DEV_MODE", False)
    key = SimpleNamespace(id=uuid.uuid4(), burst_limit_per_minute=2)
    with pytest.raises(HTTPException) as exc:
        await mcp_product.enforce_product_key_burst_limit(None, key)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_monthly_quota_counts_only_successful_product_key_calls(
    db_session, test_tenant, test_user
):
    key = MCPProductKey(
        tenant_id=test_tenant.id,
        name="quota",
        key_hash=mcp_product.hash_key("clmcp_quota_security"),
        key_prefix="clmcp_quota",
        allowed_tools=["search_caselaw"],
        monthly_call_limit=1,
        burst_limit_per_minute=10,
        created_by_user_id=test_user.id,
    )
    db_session.add(key)
    await db_session.flush()
    db_session.add(
        MCPUsageEvent(
            tenant_id=test_tenant.id,
            product_key_id=key.id,
            auth_type="product_key",
            transport="rest",
            tool_name="search_caselaw",
            status_code=200,
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await mcp_product.enforce_product_key_quota(db_session, key)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_failed_product_call_does_not_consume_monthly_quota(
    db_session, test_tenant, test_user
):
    key = MCPProductKey(
        tenant_id=test_tenant.id,
        name="failed quota",
        key_hash=mcp_product.hash_key("clmcp_failed_quota_security"),
        key_prefix="clmcp_failq",
        allowed_tools=["search_caselaw"],
        monthly_call_limit=1,
        burst_limit_per_minute=10,
        created_by_user_id=test_user.id,
    )
    db_session.add(key)
    await db_session.flush()
    db_session.add(
        MCPUsageEvent(
            tenant_id=test_tenant.id,
            product_key_id=key.id,
            auth_type="product_key",
            transport="rest",
            tool_name="search_caselaw",
            status_code=503,
        )
    )
    await db_session.commit()

    await mcp_product.enforce_product_key_quota(db_session, key)


@pytest.mark.asyncio
async def test_successful_calls_stop_at_key_dollar_budget(
    db_session, test_tenant, test_user
):
    key = MCPProductKey(
        tenant_id=test_tenant.id,
        name="budget",
        key_hash=mcp_product.hash_key("lhrk_budget_security"),
        key_prefix="lhrk_budget_",
        allowed_tools=["search_caselaw"],
        monthly_call_limit=100,
        monthly_budget_cents=45,
        unit_price_cents=45,
        burst_limit_per_minute=10,
        created_by_user_id=test_user.id,
    )
    db_session.add(key)
    await db_session.flush()
    db_session.add(
        MCPUsageEvent(
            tenant_id=test_tenant.id,
            product_key_id=key.id,
            auth_type="product_key",
            transport="rest",
            tool_name="search_caselaw",
            status_code=200,
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await mcp_product.enforce_product_key_quota(db_session, key)
    assert exc.value.status_code == 429
    assert "budget" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_expired_product_key_is_rejected(
    db_session, test_tenant, test_user, monkeypatch
):
    _enable_product(monkeypatch)
    test_tenant.is_active = True
    test_tenant.mcp_entitlement_status = "enabled"
    test_tenant.mcp_billing_status = "active"
    test_tenant.stripe_customer_id = "cus_expired"
    raw_key = "lhrk_expired_security"
    db_session.add(
        MCPProductKey(
            tenant_id=test_tenant.id,
            name="expired",
            key_hash=mcp_product.hash_key(raw_key),
            key_prefix="lhrk_expired",
            allowed_tools=["search_caselaw"],
            monthly_call_limit=100,
            burst_limit_per_minute=10,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            created_by_user_id=test_user.id,
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await mcp_product.resolve_product_key(db_session, raw_key)
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_key_assigned_to_inactive_staff_is_rejected(
    db_session, test_tenant, test_user, monkeypatch
):
    _enable_product(monkeypatch)
    test_tenant.is_active = True
    test_tenant.mcp_entitlement_status = "enabled"
    test_tenant.mcp_billing_status = "active"
    test_tenant.stripe_customer_id = "cus_inactive_assignee"
    test_user.is_active = False
    raw_key = "lhrk_inactive_assignee"
    db_session.add(
        MCPProductKey(
            tenant_id=test_tenant.id,
            name="assigned",
            key_hash=mcp_product.hash_key(raw_key),
            key_prefix="lhrk_inactiv",
            allowed_tools=["search_caselaw"],
            monthly_call_limit=100,
            burst_limit_per_minute=10,
            assigned_to_user_id=test_user.id,
            created_by_user_id=test_user.id,
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await mcp_product.resolve_product_key(db_session, raw_key)
    assert exc.value.status_code == 401
    assert "assignee" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_resolved_key_is_denied_immediately_when_tenant_deactivates(
    db_session, test_tenant, test_user, monkeypatch
):
    _enable_product(monkeypatch)
    test_tenant.is_active = True
    test_tenant.mcp_entitlement_status = "enabled"
    test_tenant.mcp_billing_status = "active"
    test_tenant.stripe_customer_id = "cus_resolve"
    raw_key = "clmcp_resolve_security"
    db_session.add(
        MCPProductKey(
            tenant_id=test_tenant.id,
            name="resolver",
            key_hash=mcp_product.hash_key(raw_key),
            key_prefix="clmcp_resolv",
            allowed_tools=["search_caselaw"],
            monthly_call_limit=100,
            burst_limit_per_minute=10,
            created_by_user_id=test_user.id,
        )
    )
    await db_session.commit()

    _, tenant = await mcp_product.resolve_product_key(db_session, raw_key)
    assert tenant.id == test_tenant.id

    test_tenant.is_active = False
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await mcp_product.resolve_product_key(db_session, raw_key)
    assert exc.value.status_code == 403


def test_upstream_headers_never_forward_user_credentials(monkeypatch):
    monkeypatch.setattr(mcp.settings, "MCP_UPSTREAM_API_KEY", "service-secret")
    assert mcp._upstream_auth_headers() == {"X-Clarity-Internal-Key": "service-secret"}


@pytest.mark.asyncio
async def test_manifest_is_hidden_while_product_disabled(monkeypatch):
    monkeypatch.setattr(mcp.settings, "MCP_PRODUCT_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        await mcp.mcp_manifest(SimpleNamespace(headers={}))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_retired_pseudo_transports_return_gone():
    with pytest.raises(HTTPException) as messages_exc:
        await mcp.mcp_messages({}, SimpleNamespace(), object())
    with pytest.raises(HTTPException) as sse_exc:
        await mcp.mcp_sse_endpoint(SimpleNamespace())
    assert messages_exc.value.status_code == 410
    assert sse_exc.value.status_code == 410
