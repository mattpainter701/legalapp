from datetime import datetime, timedelta, timezone
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.tenant import TenantSettings
from app.models.user import User


settings = get_settings()


async def _client_for(db_session: AsyncSession, user: User, billing_tier: str = "flat"):
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": user.email,
        "billing_tier": billing_tier,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = jose_jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_unlicensed_user_gets_basic_addon_portal_only(db_session, test_tenant):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="unlicensed@testfirm.com",
        full_name="Unlicensed User",
        role="user",
        is_active=True,
        license_active=False,
    )
    db_session.add(user)
    await db_session.commit()

    async with await _client_for(db_session, user, test_tenant.billing_tier) as client:
        response = await client.get("/api/auth/me")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["license_active"] is False
    assert body["enabled_modules"] == ["plugins"]
    assert body["default_route"] == "/plugins"


@pytest.mark.asyncio
async def test_intake_only_tenant_gets_intake_widget_only(db_session, test_tenant):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="intake@testfirm.com",
        role="user",
        is_active=True,
        license_active=True,
    )
    settings_row = TenantSettings(
        tenant_id=test_tenant.id,
        custom_config={"plan": "intake-only"},
    )
    db_session.add_all([user, settings_row])
    await db_session.commit()

    async with await _client_for(db_session, user, test_tenant.billing_tier) as client:
        response = await client.get("/api/auth/me")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    # Standalone Call Intake plan: no plugins/marketplace for a non-admin user;
    # upsell is handled by locked-nav teasers instead.
    assert body["enabled_modules"] == ["intake-dashboard"]
    assert body["default_route"] == "/intake/dashboard"


@pytest.mark.asyncio
async def test_accountant_can_manage_licensing_without_admin_role(
    db_session, test_tenant
):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="accountant@testfirm.com",
        role="accountant",
        is_active=True,
        license_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    async with await _client_for(db_session, user, test_tenant.billing_tier) as client:
        response = await client.get("/api/admin/licensing")

    app.dependency_overrides.clear()
    assert response.status_code == 200
