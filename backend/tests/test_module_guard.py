from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.tenant import TenantSettings

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
async def test_intake_plan_blocked_from_matters(intake_client):
    resp = await intake_client.get("/api/matters")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_general_plan_blocked_from_specialized_templates(intake_client):
    resp = await intake_client.get("/api/templates")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Module not available on your plan"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/plugins/trust-estate/estates", None),
        ("GET", "/api/plugins/domestic/cases", None),
        ("GET", "/api/plugins/mediation/cases", None),
        (
            "POST",
            "/api/plugins/commercial-legal/nda-review",
            {"skill": "nda-review", "input_text": "Confidential agreement"},
        ),
    ],
)
async def test_intake_plan_blocks_every_plugin_api_shape(
    intake_client, method, path, payload
):
    """UI hiding cannot grant intake-only tenants paid plugin APIs."""
    resp = await intake_client.request(method, path, json=payload)

    assert resp.status_code == 403
    assert resp.json() == {"detail": "Module not available on your plan"}


@pytest.mark.asyncio
async def test_intake_plan_catalog_read_does_not_grant_plugin_apis(intake_client):
    catalog = await intake_client.get("/api/plugins")
    nested_read = await intake_client.get("/api/plugins/trust-estate/estates")
    execution = await intake_client.post(
        "/api/plugins/commercial-legal/nda-review",
        json={"skill": "nda-review", "input_text": "Confidential agreement"},
    )

    assert catalog.status_code == 200
    assert catalog.json()["plugins"]
    assert nested_read.status_code == 403
    assert execution.status_code == 403


@pytest.mark.asyncio
async def test_intake_only_allowed_on_intake(intake_client):
    resp = await intake_client.get(
        "/api/intake/dashboard/recent-callers", params={"limit": 10}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_intake_only_allowed_on_tasks(intake_client):
    # Standalone call-intake customers manage lead follow-up through tasks.
    resp = await intake_client.get("/api/tasks")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_me_exposes_call_tracker_and_tasks_navigation(
    intake_client, db_session, test_tenant
):
    db_session.add(
        TenantSettings(tenant_id=test_tenant.id, custom_config={"plan": "intake-only"})
    )
    await db_session.commit()
    resp = await intake_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert set(resp.json()["enabled_modules"]) == {
        "tasks",
        "intake-dashboard",
        "admin",
    }
    assert resp.json()["default_route"] == "/intake/dashboard"


@pytest.mark.asyncio
async def test_full_platform_token_not_blocked(client):
    # default conftest token has no plan claim -> full-platform -> allowed
    resp = await client.get("/api/matters")
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_full_platform_token_keeps_plugin_catalog_access(client):
    resp = await client.get("/api/plugins")

    assert resp.status_code == 200
    assert resp.json()["plugins"]
