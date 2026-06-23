import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.config import get_settings
from app.database import get_db
from app.main import app
from app.models.rbac import Role, UserRole
from app.models.user import User

settings = get_settings()


async def _admin_client(db_session, test_tenant):
    admin = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="admin@testfirm.com",
        role="user",
    )
    db_session.add(admin)
    role = Role(
        tenant_id=test_tenant.id,
        name="Admins",
        capabilities=["admin_settings", "manage_roles"],
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(UserRole(user_id=admin.id, role_id=role.id, source="manual"))
    await db_session.commit()
    payload = {
        "sub": str(admin.id),
        "tenant_id": str(admin.tenant_id),
        "role": "user",
        "email": admin.email,
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
    ), admin


@pytest.mark.asyncio
async def test_create_and_list_role(db_session, test_tenant):
    client, _ = await _admin_client(db_session, test_tenant)
    async with client:
        created = await client.post(
            "/api/admin/roles",
            json={
                "name": "Paralegal",
                "description": "Paralegal staff",
                "capabilities": ["manage_matters", "manage_documents"],
            },
        )
        listed = await client.get("/api/admin/roles")
    app.dependency_overrides.clear()
    assert created.status_code == 201
    assert created.json()["name"] == "Paralegal"
    names = [r["name"] for r in listed.json()]
    assert "Paralegal" in names


@pytest.mark.asyncio
async def test_create_role_rejects_unknown_capability(db_session, test_tenant):
    client, _ = await _admin_client(db_session, test_tenant)
    async with client:
        resp = await client.post(
            "/api/admin/roles", json={"name": "Bad", "capabilities": ["not_real"]}
        )
    app.dependency_overrides.clear()
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_assign_roles_rejects_cross_tenant_user(db_session, test_tenant):
    import uuid as _uuid
    from app.models.tenant import Tenant

    client, _ = await _admin_client(db_session, test_tenant)
    # A user in a DIFFERENT tenant
    other_tenant = Tenant(
        id=_uuid.uuid4(),
        name="Other Firm",
        domain="otherfirm.com",
        billing_tier="flat",
    )
    db_session.add(other_tenant)
    await db_session.flush()
    victim = User(
        id=_uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="victim@otherfirm.com",
        role="user",
    )
    db_session.add(victim)
    await db_session.commit()

    async with client:
        resp = await client.put(
            f"/api/admin/roles/assign/{victim.id}", json={"role_ids": []}
        )
    app.dependency_overrides.clear()
    assert resp.status_code == 404
