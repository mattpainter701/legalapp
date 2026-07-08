import uuid
from decimal import Decimal

import pytest

from app.models.user import User


@pytest.mark.asyncio
async def test_admin_users_returns_role_permission_fields(client, db_session, test_user):
    test_user.default_billing_rate = Decimal("250.00")
    test_user.payg_monthly_budget = Decimal("500.00")
    test_user.license_active = False
    await db_session.commit()

    resp = await client.get("/api/admin/users")

    assert resp.status_code == 200
    user = next(u for u in resp.json()["users"] if u["id"] == str(test_user.id))
    assert user["role"] == "admin"
    assert user["license_active"] is False
    assert user["default_billing_rate"] == 250.0
    assert user["payg_monthly_budget"] == 500.0


@pytest.mark.asyncio
async def test_cannot_remove_or_deactivate_last_active_admin(client, test_user):
    demote = await client.patch(
        f"/api/admin/users/{test_user.id}", json={"role": "user"}
    )
    assert demote.status_code == 400
    assert "last active admin" in demote.json()["detail"]

    deactivate = await client.delete(f"/api/admin/users/{test_user.id}")
    assert deactivate.status_code == 400
    assert "last active admin" in deactivate.json()["detail"]


@pytest.mark.asyncio
async def test_can_demote_admin_when_another_active_admin_exists(
    client, db_session, test_tenant, test_user
):
    other_admin = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="second-admin@testfirm.com",
        full_name="Second Admin",
        role="admin",
        is_active=True,
    )
    db_session.add(other_admin)
    await db_session.commit()

    resp = await client.patch(
        f"/api/admin/users/{test_user.id}", json={"role": "user"}
    )

    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


@pytest.mark.asyncio
async def test_invite_rejects_invalid_role(client):
    resp = await client.post(
        "/api/admin/users/invite",
        json={"email": "bad-role@testfirm.com", "role": "owner"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "role must be 'admin' or 'user'"
