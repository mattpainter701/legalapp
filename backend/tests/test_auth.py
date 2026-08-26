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


@pytest.mark.asyncio
async def test_optional_demo_metadata_does_not_break_auth_profile(caplog):
    """A demo-table problem must not make a valid session look logged out."""
    from app.routers.auth import _active_demo_session

    class BrokenDemoLookup:
        async def scalar(self, _statement):
            raise RuntimeError("demo_sessions relation is unavailable")

    assert await _active_demo_session(BrokenDemoLookup(), uuid.uuid4()) is None
    assert "optional demo metadata" in caplog.text


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
        test_user.professional_role = "Attorney"
        test_user.job_title = "Litigation Counsel"
        test_user.office_location = "Chicago"
        test_user.primary_jurisdictions = ["Illinois", "Northern District of Illinois"]
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == test_user.email
        assert data["role"] == test_user.role
        assert data["tenant_id"] == str(test_tenant.id)
        assert data["professional_role"] == "Attorney"
        assert data["primary_jurisdictions"] == [
            "Illinois",
            "Northern District of Illinois",
        ]

    async def test_me_profile_patch_only_updates_professional_fields(
        self, client, test_user
    ):
        original_name = test_user.full_name
        resp = await client.patch(
            "/api/auth/me",
            json={
                "professional_role": "Paralegal",
                "job_title": "Senior Paralegal",
                "office_location": "Fargo",
                "primary_jurisdictions": ["North Dakota", "North Dakota"],
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["professional_role"] == "Paralegal"
        assert data["primary_jurisdictions"] == ["North Dakota"]
        assert data["full_name"] == original_name

    async def test_enabling_privacy_mode_revokes_workspace_mcp_grants(
        self, client, db_session, test_tenant, test_user
    ):
        from app.models.workspace_mcp_grant import WorkspaceMCPGrant

        grant = WorkspaceMCPGrant(
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            client_id="claude-desktop",
            client_name="Claude",
            scopes=["matters:read"],
            consent_version="v1",
            consent_sha256="a" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        research_grant = WorkspaceMCPGrant(
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            client_id="research.claude-desktop",
            client_name="Claude Research",
            scopes=["research:read"],
            consent_version="research-mcp-v1",
            consent_sha256="r" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db_session.add_all([grant, research_grant])
        await db_session.commit()

        response = await client.patch("/api/auth/me", json={"privacy_mode": True})

        assert response.status_code == 200, response.text
        assert response.json()["privacy_mode"] is True
        await db_session.refresh(grant)
        assert grant.status == "revoked"
        assert grant.revoked_at is not None
        assert grant.revocation_reason == "Privacy Mode enabled"
        await db_session.refresh(research_grant)
        assert research_grant.status == "active"
        assert research_grant.revoked_at is None

    async def test_me_profile_patch_restores_rls_context_after_commit(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        from app.routers import auth
        from app.schemas.auth import UserProfileUpdate

        tenant_id = uuid.uuid4()
        user = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            # Stands in for a Tenant row, so it must carry the columns /me
            # reads -- including the payment-health fields the billing banner
            # depends on.
            tenant=SimpleNamespace(
                billing_tier="flat",
                stripe_subscription_status="active",
                mcp_billing_status="active",
            ),
            email="lawyer@example.test",
            full_name="Test Lawyer",
            role="attorney",
            is_active=True,
            license_active=True,
            premium_ai_enabled=False,
            created_at=datetime.now(timezone.utc),
            professional_role=None,
            job_title=None,
            office_location=None,
            primary_jurisdictions=[],
            privacy_mode=False,
        )
        events = []

        class FakeSession:
            async def commit(self):
                events.append("commit")

            async def refresh(self, refreshed_user):
                assert refreshed_user is user
                events.append("refresh")

        async def current_user(_request, _db):
            return user

        async def tenant_context(_db, value):
            events.append(("tenant", value))

        async def enabled_modules(_db, _tenant_id, *, user):
            return ["chat"], "/chat"

        async def plan_meta(_db, _tenant_id):
            return "full-platform", None

        monkeypatch.setattr(auth, "get_current_user", current_user)
        monkeypatch.setattr(auth, "set_tenant_context", tenant_context)
        monkeypatch.setattr(auth, "resolve_enabled_modules", enabled_modules)
        monkeypatch.setattr(auth, "resolve_plan_meta", plan_meta)

        response = await auth.update_me(
            UserProfileUpdate(professional_role="Attorney"),
            SimpleNamespace(),
            FakeSession(),
        )

        assert response.professional_role == "Attorney"
        assert events == [
            ("tenant", str(tenant_id)),
            "commit",
            ("tenant", str(tenant_id)),
            "refresh",
        ]

    async def test_enabling_privacy_mode_revokes_and_cleans_up_grants(
        self, monkeypatch
    ):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.routers import auth
        from app.schemas.auth import UserProfileUpdate
        from app.services import workspace_mcp_oauth

        tenant_id = uuid.uuid4()
        user = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            tenant=SimpleNamespace(
                billing_tier="flat",
                stripe_subscription_status="active",
                mcp_billing_status="active",
            ),
            email="lawyer@example.test",
            full_name="Test Lawyer",
            role="attorney",
            is_active=True,
            license_active=True,
            premium_ai_enabled=False,
            created_at=datetime.now(timezone.utc),
            professional_role=None,
            job_title=None,
            office_location=None,
            primary_jurisdictions=[],
            privacy_mode=False,
        )
        grant = SimpleNamespace(
            id=uuid.uuid4(),
            client_id="claude-desktop",
            status="active",
            revoked_at=None,
            revoked_by_user_id=None,
            revocation_reason=None,
        )

        class Result:
            def all(self):
                return [grant]

        class FakeSession:
            async def scalars(self, _statement):
                return Result()

            async def execute(self, _statement, _parameters=None):
                return None

            async def commit(self):
                return None

            async def refresh(self, _user):
                return None

        async def current_user(_request, _db):
            return user

        async def tenant_context(_db, _value):
            return None

        async def enabled_modules(_db, _tenant_id, *, user):
            return ["chat"], "/chat"

        async def plan_meta(_db, _tenant_id):
            return "full-platform", None

        audit = AsyncMock()
        cleanup = AsyncMock(side_effect=RuntimeError("Redis unavailable"))
        monkeypatch.setattr(auth, "get_current_user", current_user)
        monkeypatch.setattr(auth, "set_tenant_context", tenant_context)
        monkeypatch.setattr(auth, "resolve_enabled_modules", enabled_modules)
        monkeypatch.setattr(auth, "resolve_plan_meta", plan_meta)
        monkeypatch.setattr(workspace_mcp_oauth, "append_workspace_mcp_audit", audit)
        monkeypatch.setattr(
            workspace_mcp_oauth, "revoke_workspace_grant_runtime", cleanup
        )

        response = await auth.update_me(
            UserProfileUpdate(privacy_mode=True), SimpleNamespace(), FakeSession()
        )

        assert response.privacy_mode is True
        assert grant.status == "revoked"
        assert grant.revoked_by_user_id == user.id
        assert grant.revocation_reason == "Privacy Mode enabled"
        audit.assert_awaited_once()
        cleanup.assert_awaited_once()

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
