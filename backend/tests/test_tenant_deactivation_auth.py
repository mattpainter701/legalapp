"""Tenant suspension is an immediate boundary for every user-session path."""

from types import SimpleNamespace

import pytest

from app.main import app
from app.routers import auth


async def _suspend(db_session, tenant) -> None:
    tenant.is_active = False
    await db_session.commit()


@pytest.mark.asyncio
async def test_inactive_tenant_rejects_existing_application_token(
    client, db_session, test_tenant
):
    await _suspend(db_session, test_tenant)

    response = await client.get("/api/auth/me")

    assert response.status_code == 403
    assert response.json()["detail"] == "Tenant account is inactive"


@pytest.mark.asyncio
async def test_inactive_tenant_cannot_password_login(
    client, db_session, test_tenant, test_user
):
    test_user.password_hash = auth._hash_password("correct horse battery staple")
    await db_session.commit()
    await _suspend(db_session, test_tenant)

    response = await client.post(
        "/api/auth/login",
        json={
            "email": test_user.email,
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Tenant account is inactive"
    assert "access_token" not in response.cookies


@pytest.mark.asyncio
async def test_inactive_tenant_rejects_oauth_exchange(
    client, db_session, test_tenant, test_user
):
    application_token = auth._create_access_token(test_user, test_tenant)
    request = SimpleNamespace(app=app)
    callback_code = await auth._save_callback_token(request, application_token)
    await _suspend(db_session, test_tenant)

    response = await client.post(
        "/api/auth/oauth/exchange", json={"code": callback_code}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Tenant account is inactive"
    assert "access_token" not in response.cookies


@pytest.mark.asyncio
async def test_inactive_tenant_rejects_refresh_and_revokes_family(
    client, db_session, test_tenant, test_user, test_redis
):
    request = SimpleNamespace(app=app)
    refresh_token = await auth._create_refresh_token(request, test_user)
    raw = await test_redis.get(auth._refresh_key(refresh_token))
    family = auth._json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)[
        "family"
    ]
    client.cookies.set("refresh_token", refresh_token)
    await _suspend(db_session, test_tenant)

    response = await client.post("/api/auth/refresh")

    assert response.status_code == 403
    assert response.json()["detail"] == "Tenant account is inactive"
    assert not await test_redis.exists(auth._refresh_family_key(family))
