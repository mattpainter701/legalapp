import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.tenant import TenantSettings
from app.routers import platform as platform_router

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
    assert any(p["id"] == "intake-only" for p in resp.json()["plans"])
