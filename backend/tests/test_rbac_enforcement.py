import uuid

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.database import get_db
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.services.access_control import require_capability

settings = get_settings()


def _token(user):
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": user.email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jose_jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


@pytest.mark.asyncio
async def test_require_capability_allows_and_denies(db_session, test_tenant):
    app = FastAPI()

    @app.get("/needs-billing")
    async def needs_billing(user=Depends(require_capability("view_billing"))):
        return {"ok": True}

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    allowed = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="acct@testfirm.com",
        role="user",
    )
    denied = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="plain@testfirm.com",
        role="user",
    )
    db_session.add_all([allowed, denied])
    role = Role(tenant_id=test_tenant.id, name="Biller", capabilities=["view_billing"])
    db_session.add(role)
    await db_session.flush()
    db_session.add(UserRole(user_id=allowed.id, role_id=role.id, source="manual"))
    await db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r_allow = await c.get(
            "/needs-billing", headers={"Authorization": f"Bearer {_token(allowed)}"}
        )
        r_deny = await c.get(
            "/needs-billing", headers={"Authorization": f"Bearer {_token(denied)}"}
        )

    assert r_allow.status_code == 200
    assert r_deny.status_code == 403
