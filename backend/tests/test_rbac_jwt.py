import uuid

import pytest
from jose import jwt as jose_jwt

from app.config import get_settings
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.models.tenant import Tenant
from app.routers.auth import _issue_access_token

settings = get_settings()


@pytest.mark.asyncio
async def test_issued_token_carries_caps(db_session, test_tenant):
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="capuser@testfirm.com",
        role="user",
    )
    db_session.add(user)
    role = Role(
        tenant_id=test_tenant.id, name="Matters", capabilities=["manage_matters"]
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        UserRole(
            user_id=user.id, role_id=role.id, source="manual", tenant_id=test_tenant.id
        )
    )
    await db_session.commit()

    tenant = await db_session.get(Tenant, test_tenant.id)
    token = await _issue_access_token(db_session, user, tenant)
    payload = jose_jwt.decode(
        token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
    )
    assert "manage_matters" in payload["caps"]
