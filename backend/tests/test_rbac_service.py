import uuid

import pytest

from app.models.rbac import Role, UserRole
from app.models.user import User
from app.services.rbac_service import get_user_capabilities, seed_system_roles


@pytest.mark.asyncio
async def test_capabilities_union_across_roles(db_session, test_tenant):
    user = User(
        id=uuid.uuid4(), tenant_id=test_tenant.id, email="u@testfirm.com", role="user"
    )
    db_session.add(user)
    r1 = Role(tenant_id=test_tenant.id, name="A", capabilities=["manage_matters"])
    r2 = Role(
        tenant_id=test_tenant.id,
        name="B",
        capabilities=["manage_matters", "view_billing"],
    )
    db_session.add_all([r1, r2])
    await db_session.flush()
    db_session.add_all(
        [
            UserRole(user_id=user.id, role_id=r1.id, source="manual"),
            UserRole(user_id=user.id, role_id=r2.id, source="group_sync"),
        ]
    )
    await db_session.commit()

    caps = await get_user_capabilities(db_session, user.id)
    assert caps == {"manage_matters", "view_billing"}


@pytest.mark.asyncio
async def test_seed_system_roles_idempotent(db_session, test_tenant):
    await seed_system_roles(db_session, test_tenant.id)
    await seed_system_roles(db_session, test_tenant.id)  # second call no-ops
    from sqlalchemy import select, func

    count = await db_session.scalar(
        select(func.count())
        .select_from(Role)
        .where(Role.tenant_id == test_tenant.id, Role.is_system.is_(True))
    )
    assert count == 4


@pytest.mark.asyncio
async def test_count_admin_capable_users(db_session, test_tenant):
    from app.services.rbac_service import count_admin_capable_users

    admin_role = Role(
        tenant_id=test_tenant.id, name="Admins", capabilities=["admin_settings"]
    )
    db_session.add(admin_role)
    u = User(
        id=uuid.uuid4(), tenant_id=test_tenant.id, email="a@testfirm.com", role="user"
    )
    db_session.add(u)
    await db_session.flush()
    db_session.add(UserRole(user_id=u.id, role_id=admin_role.id, source="manual"))
    await db_session.commit()

    assert await count_admin_capable_users(db_session, test_tenant.id) == 1
