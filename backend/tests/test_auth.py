"""
Tests for auth endpoints (JWT decode, token shape, user-info route).
OAuth redirect flows are integration-tested separately; these tests cover
the JWT helper and the /auth/me endpoint.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.config import get_settings

settings = get_settings()


def make_token(
    user_id: str,
    tenant_id: str,
    role: str = "attorney",
    billing_tier: str = "payg",
    minutes: int = 60,
    secret: str | None = None,
) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "email": "attorney@testfirm.com",
        "billing_tier": billing_tier,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes),
    }
    return jwt.encode(
        payload, secret or settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )


# ---------------------------------------------------------------------------
# Token generation / decode
# ---------------------------------------------------------------------------


class TestJWTHelpers:
    def test_token_roundtrip(self):
        uid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        token = make_token(uid, tid)
        decoded = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert decoded["sub"] == uid
        assert decoded["tenant_id"] == tid

    def test_token_contains_role(self):
        token = make_token(str(uuid.uuid4()), str(uuid.uuid4()), role="admin")
        decoded = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert decoded["role"] == "admin"

    def test_token_contains_billing_tier(self):
        token = make_token(str(uuid.uuid4()), str(uuid.uuid4()), billing_tier="flat")
        decoded = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert decoded["billing_tier"] == "flat"

    def test_expired_token_raises(self):
        from jose import ExpiredSignatureError

        token = make_token(str(uuid.uuid4()), str(uuid.uuid4()), minutes=-1)
        with pytest.raises(ExpiredSignatureError):
            jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

    def test_wrong_secret_raises(self):
        from jose import JWTError

        token = make_token(str(uuid.uuid4()), str(uuid.uuid4()), secret="wrong-secret")
        with pytest.raises(JWTError):
            jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


# ---------------------------------------------------------------------------
# /auth/me endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAuthMe:
    async def test_me_returns_user_info(self, client, test_user, test_tenant):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == test_user.email
        assert data["role"] == test_user.role
        assert data["tenant_id"] == str(test_tenant.id)

    async def test_me_no_token_returns_401(self, db_session):
        from httpx import ASGITransport, AsyncClient
        from app.main import app
        from app.database import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/api/auth/me")
        app.dependency_overrides.clear()
        assert resp.status_code == 401

    async def test_me_invalid_token_returns_401(self, db_session):
        from httpx import ASGITransport, AsyncClient
        from app.main import app
        from app.database import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer not.a.valid.jwt"},
        ) as ac:
            resp = await ac.get("/api/auth/me")
        app.dependency_overrides.clear()
        assert resp.status_code == 401
