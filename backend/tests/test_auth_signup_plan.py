import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User


@pytest_asyncio.fixture
async def public_client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_public_signup_provisions_intake_tenant(public_client, db_session):
    resp = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "intake-only",
            "firm_name": "Reception Co",
            "email": "owner@reception.co",
            "password": "longenoughpw123",
            "full_name": "Owner One",
            "staff_size": 4,
            "address": "100 First Customer Way",
            "phone": "+1 701-555-0101",
        },
    )
    assert resp.status_code == 201
    user = (
        await db_session.execute(select(User).where(User.email == "owner@reception.co"))
    ).scalar_one()
    assert user.role == "admin"
    ts = (
        await db_session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
        )
    ).scalar_one()
    assert ts.custom_config["plan"] == "intake-only"
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one()
    assert tenant.billing_tier == "intake_trial"
    assert tenant.staff_size == 4
    assert tenant.address == "100 First Customer Way"
    assert tenant.phone == "+1 701-555-0101"


@pytest.mark.asyncio
async def test_public_signup_provisions_mcp_tenant(public_client, db_session):
    resp = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "mcp-only",
            "firm_name": "Research API Co",
            "email": "owner@research-api.co",
            "password": "longenoughpw123",
            "full_name": "Owner Two",
        },
    )
    assert resp.status_code == 201
    user = (
        await db_session.execute(
            select(User).where(User.email == "owner@research-api.co")
        )
    ).scalar_one()
    assert user.role == "admin"
    ts = (
        await db_session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == user.tenant_id)
        )
    ).scalar_one()
    assert ts.custom_config["plan"] == "mcp-only"
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one()
    assert tenant.billing_tier == "payg"


@pytest.mark.asyncio
async def test_signup_rejects_non_public_plan(public_client):
    resp = await public_client.post(
        "/api/auth/signup/plan",
        json={
            "plan": "full-platform",
            "firm_name": "X",
            "email": "x@y.co",
            "password": "longenoughpw123",
            "full_name": "X",
        },
    )
    assert resp.status_code == 403
