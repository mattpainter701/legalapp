import uuid

import pytest

from app.models.rbac import Role, UserRole
from app.models.user import User


@pytest.mark.asyncio
async def test_role_and_user_role_persist(db_session, test_tenant):
    role = Role(
        tenant_id=test_tenant.id,
        name="Paralegal",
        description="Paralegal staff",
        capabilities=["manage_matters", "manage_documents"],
        is_system=False,
    )
    db_session.add(role)
    await db_session.flush()

    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="para@testfirm.com",
        role="user",
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(UserRole(user_id=user.id, role_id=role.id, source="manual"))
    await db_session.commit()

    assert role.capabilities == ["manage_matters", "manage_documents"]
    assert role.is_system is False
