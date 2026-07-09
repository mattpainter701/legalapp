import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.mcp_product import MCPProductKey, MCPUsageEvent
from app.models.operator_audit import OperatorAuditLog
from app.models.tenant import TenantSettings
from app.routers import platform as platform_router
from app.services.mcp_product import hash_key

TEST_PLATFORM_KEY = "test-platform-key-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


@pytest.mark.asyncio
async def test_set_tenant_plan(client: AsyncClient, db_session, test_tenant):
    platform_router.settings.PLATFORM_SECRET_KEY = TEST_PLATFORM_KEY
    headers = {"X-Platform-Key": TEST_PLATFORM_KEY}
    resp = await client.put(
        f"/api/platform/tenants/{test_tenant.id}",
        json={"plan": "intake-only"},
        headers=headers,
    )
    assert resp.status_code == 200
    ts = (
        await db_session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id)
        )
    ).scalar_one()
    assert ts.custom_config["plan"] == "intake-only"


@pytest.mark.asyncio
async def test_clear_tenant_plan_and_audit_update(
    client: AsyncClient, db_session, test_tenant
):
    platform_router.settings.PLATFORM_SECRET_KEY = TEST_PLATFORM_KEY
    headers = {"X-Platform-Key": TEST_PLATFORM_KEY}
    ts = TenantSettings(
        tenant_id=test_tenant.id,
        custom_config={"plan": "intake-only"},
    )
    db_session.add(ts)
    await db_session.commit()

    resp = await client.put(
        f"/api/platform/tenants/{test_tenant.id}",
        json={"plan": None, "billing_tier": "payg"},
        headers=headers,
    )

    assert resp.status_code == 200
    await db_session.refresh(ts)
    await db_session.refresh(test_tenant)
    assert "plan" not in (ts.custom_config or {})
    assert test_tenant.billing_tier == "payg"

    logs = (await db_session.execute(select(OperatorAuditLog))).scalars().all()
    assert [log.action for log in logs] == ["tenant.updated"]
    assert logs[0].resource_type == "tenant"
    assert logs[0].resource_id == str(test_tenant.id)
    assert logs[0].metadata_json["changes"]["plan"] == {
        "from": "intake-only",
        "to": None,
    }
    assert logs[0].metadata_json["changes"]["billing_tier"]["to"] == "payg"


@pytest.mark.asyncio
async def test_set_unknown_plan_rejected(client: AsyncClient, test_tenant):
    platform_router.settings.PLATFORM_SECRET_KEY = TEST_PLATFORM_KEY
    headers = {"X-Platform-Key": TEST_PLATFORM_KEY}
    resp = await client.put(
        f"/api/platform/tenants/{test_tenant.id}",
        json={"plan": "bogus"},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_plans(client: AsyncClient):
    platform_router.settings.PLATFORM_SECRET_KEY = TEST_PLATFORM_KEY
    headers = {"X-Platform-Key": TEST_PLATFORM_KEY}
    resp = await client.get("/api/platform/plans", headers=headers)
    assert resp.status_code == 200
    plan_ids = {p["id"] for p in resp.json()["plans"]}
    assert "intake-only" in plan_ids
    assert "mcp-only" in plan_ids


@pytest.mark.asyncio
async def test_platform_mcp_overview(
    client: AsyncClient, db_session, test_tenant, test_user
):
    platform_router.settings.PLATFORM_SECRET_KEY = TEST_PLATFORM_KEY
    headers = {"X-Platform-Key": TEST_PLATFORM_KEY}
    key = MCPProductKey(
        tenant_id=test_tenant.id,
        name="Claude Desktop",
        key_hash=hash_key("clmcp_test_secret"),
        key_prefix="clmcp_test",
        allowed_tools=None,
        monthly_call_limit=500,
        created_by_user_id=test_user.id,
    )
    db_session.add(key)
    await db_session.flush()
    db_session.add(
        MCPUsageEvent(
            tenant_id=test_tenant.id,
            product_key_id=key.id,
            user_id=test_user.id,
            auth_type="product_key",
            transport="messages",
            tool_name="search_caselaw",
            status_code=200,
            result_count=3,
        )
    )
    await db_session.commit()

    resp = await client.get("/api/platform/mcp", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["overview"]["active_keys"] == 1
    assert payload["overview"]["calls_30d"] == 1
    assert payload["keys"][0]["name"] == "Claude Desktop"
    assert payload["keys"][0]["billing"]["line_item"] == "MCP usage"
    assert payload["connection"]["auth_header"] == "X-MCP-API-Key"
