import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.mcp_product import MCPProductKey, MCPUsageEvent
from app.models.llm_routing_profile import LLMRoutingProfile
from app.models.operator_audit import OperatorAuditLog
from app.models.tenant import TenantSettings
from app.services.mcp_product import hash_key
from tests.platform_auth_helpers import platform_headers

TEST_PLATFORM_KEY = "test-platform-key-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


@pytest.mark.asyncio
async def test_set_tenant_plan(client: AsyncClient, db_session, test_tenant):
    headers = platform_headers()
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
    headers = platform_headers()
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

    logs = [
        row
        for row in (await db_session.execute(select(OperatorAuditLog))).scalars().all()
        if row.action != "platform.request"
    ]
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
    headers = platform_headers()
    resp = await client.put(
        f"/api/platform/tenants/{test_tenant.id}",
        json={"plan": "bogus"},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_assign_and_clear_tenant_routing_profile(
    client: AsyncClient, db_session, test_tenant
):
    profile = LLMRoutingProfile(
        name="Tenant profile",
        standard_route={"model": "standard"},
        premium_route={"model": "premium"},
        standard_allow_matter_context=True,
        premium_allow_matter_context=False,
        is_active=True,
        activation={
            "status": "active",
            "aliases": {
                "standard": "clarity-standard-rtenant",
                "premium": "clarity-premium-rtenant",
            },
        },
    )
    db_session.add(profile)
    await db_session.commit()

    assigned = await client.put(
        f"/api/platform/tenants/{test_tenant.id}",
        json={"llm_routing_profile_id": str(profile.id)},
        headers=platform_headers(),
    )
    assert assigned.status_code == 200

    detail = await client.get(
        f"/api/platform/tenants/{test_tenant.id}", headers=platform_headers()
    )
    assert detail.status_code == 200
    assert detail.json()["llm_config"]["routing_profile"] == {
        "id": str(profile.id),
        "name": "Tenant profile",
        "is_default": False,
        "is_active": True,
        "assignable": True,
        "standard_allow_matter_context": True,
        "premium_allow_matter_context": False,
    }

    cleared = await client.put(
        f"/api/platform/tenants/{test_tenant.id}",
        json={"llm_routing_profile_id": None},
        headers=platform_headers(),
    )
    assert cleared.status_code == 200
    settings = (
        await db_session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id)
        )
    ).scalar_one()
    assert settings.llm_routing_profile_id is None


@pytest.mark.asyncio
async def test_tenant_routing_profile_assignment_rejects_invalid_or_unactivated(
    client: AsyncClient, db_session, test_tenant
):
    invalid = await client.put(
        f"/api/platform/tenants/{test_tenant.id}",
        json={"llm_routing_profile_id": "not-a-uuid"},
        headers=platform_headers(),
    )
    assert invalid.status_code == 400

    blank = LLMRoutingProfile(
        name="Unactivated profile",
        standard_route={},
        premium_route={},
        is_active=True,
    )
    db_session.add(blank)
    await db_session.commit()
    unactivated = await client.put(
        f"/api/platform/tenants/{test_tenant.id}",
        json={"llm_routing_profile_id": str(blank.id)},
        headers=platform_headers(),
    )
    assert unactivated.status_code == 400


@pytest.mark.asyncio
async def test_operator_can_suspend_mcp_entitlement(
    client: AsyncClient, db_session, test_tenant
):
    test_tenant.mcp_entitlement_status = "enabled"
    test_tenant.mcp_billing_status = "active"
    await db_session.commit()
    resp = await client.put(
        f"/api/platform/tenants/{test_tenant.id}",
        json={"mcp_entitlement_status": "suspended"},
        headers=platform_headers(),
    )
    assert resp.status_code == 200
    await db_session.refresh(test_tenant)
    assert test_tenant.mcp_entitlement_status == "suspended"


@pytest.mark.asyncio
async def test_list_plans(client: AsyncClient):
    headers = platform_headers()
    resp = await client.get("/api/platform/plans", headers=headers)
    assert resp.status_code == 200
    plan_ids = {p["id"] for p in resp.json()["plans"]}
    assert "intake-only" in plan_ids
    assert "mcp-only" in plan_ids


@pytest.mark.asyncio
async def test_platform_mcp_overview(
    client: AsyncClient, db_session, test_tenant, test_user
):
    headers = platform_headers()
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
