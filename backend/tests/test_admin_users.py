import uuid
from decimal import Decimal

import pytest

from app.models.rbac import Role, UserRole
from app.models.user import User


async def _assign_role(db_session, tenant_id, user_id, name, capabilities):
    role = Role(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        capabilities=capabilities,
        is_system=False,
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        UserRole(
            user_id=user_id,
            role_id=role.id,
            tenant_id=tenant_id,
            source="manual",
        )
    )
    return role


@pytest.mark.asyncio
async def test_admin_users_returns_billing_and_role_assignment_fields(
    client, db_session, test_tenant
):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="billing-user@testfirm.com",
        full_name="Billing User",
        role="user",
        is_active=True,
        license_active=False,
        default_billing_rate=Decimal("250.00"),
        payg_monthly_budget=Decimal("500.00"),
    )
    db_session.add(user)
    await db_session.flush()
    role = await _assign_role(
        db_session,
        test_tenant.id,
        user.id,
        "Billing Manager",
        ["view_billing", "manage_billing"],
    )
    await db_session.commit()

    resp = await client.get("/api/admin/users")

    assert resp.status_code == 200
    payload = resp.json()["users"]
    row = next(u for u in payload if u["id"] == str(user.id))
    assert row["role"] == "user"
    assert row["license_active"] is False
    assert row["default_billing_rate"] == 250.0
    assert row["payg_monthly_budget"] == 500.0
    assert row["role_ids"] == [str(role.id)]
    assert row["roles"] == [{"id": str(role.id), "name": "Billing Manager"}]


@pytest.mark.asyncio
async def test_deactivate_admin_settings_holder_when_legacy_admin_remains(
    client, db_session, test_tenant
):
    target = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="settings-admin@testfirm.com",
        role="user",
        is_active=True,
    )
    db_session.add(target)
    await db_session.flush()
    await _assign_role(
        db_session,
        test_tenant.id,
        target.id,
        "Settings Admin",
        ["admin_settings"],
    )
    await db_session.commit()

    resp = await client.delete(f"/api/admin/users/{target.id}")

    assert resp.status_code == 204
    await db_session.refresh(target)
    assert target.is_active is False


@pytest.mark.asyncio
async def test_deactivate_legacy_admin_when_another_legacy_admin_remains(
    client, db_session, test_tenant
):
    target = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="legacy-admin@testfirm.com",
        role="admin",
        is_active=True,
    )
    db_session.add(target)
    await db_session.commit()

    resp = await client.delete(f"/api/admin/users/{target.id}")

    assert resp.status_code == 204
    await db_session.refresh(target)
    assert target.is_active is False


@pytest.mark.asyncio
async def test_patch_blocks_self_demoting_last_effective_admin(client, test_user):
    resp = await client.patch(f"/api/admin/users/{test_user.id}", json={"role": "user"})

    assert resp.status_code == 400
    assert "last active admin" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_accepts_accountant_role(client, db_session, test_tenant):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="accountant-user@testfirm.com",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    resp = await client.patch(
        f"/api/admin/users/{user.id}", json={"role": "accountant"}
    )

    assert resp.status_code == 200
    assert resp.json()["role"] == "accountant"


@pytest.mark.asyncio
async def test_invite_rejects_invalid_role(client):
    resp = await client.post(
        "/api/admin/users/invite",
        json={"email": "bad-role@testfirm.com", "role": "owner"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "role must be 'admin', 'accountant', or 'user'"
