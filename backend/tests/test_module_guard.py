from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.config import get_settings
from app.database import get_db
from app.main import app

settings = get_settings()


def _token(tenant_id, user_id, plan):
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": "admin",
        "email": "a@b.com",
        "billing_tier": "intake_trial",
        "plan": plan,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jose_jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


@pytest_asyncio.fixture
async def intake_client(db_session, test_tenant, test_user):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    token = _token(test_tenant.id, test_user.id, "intake-only")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_intake_only_blocked_from_matters(intake_client):
    resp = await intake_client.get("/api/matters")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Module not available on your plan"


@pytest.mark.asyncio
async def test_intake_only_allowed_on_intake(intake_client):
    resp = await intake_client.get(
        "/api/intake/dashboard/recent-callers", params={"limit": 10}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_full_platform_token_not_blocked(client):
    # default conftest token has no plan claim -> full-platform -> allowed
    resp = await client.get("/api/matters")
    assert resp.status_code != 403
