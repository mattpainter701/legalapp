import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.database import get_db
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User
from app.routers import auth as auth_router


class _ProviderTokenResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "access_token": "provider-access-token",
            "id_token": "provider-id-token",
        }


async def _oauth_client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _mock_oauth_callback(monkeypatch, provider: str, claims: dict) -> None:
    async def consume_state(_request, _state):
        return True, {
            "signup": None,
            "nonce": "expected-nonce",
            "pkce_verifier": "pkce-verifier",
        }

    async def provider_post(_self, _url, **_kwargs):
        return _ProviderTokenResponse()

    async def verify_token(_raw_token, **_kwargs):
        return claims

    async def issue_access_token(_db, _user, _tenant):
        return "application-jwt"

    async def save_replay(_request, _state, _provider_code, _token):
        return None

    async def save_callback_token(_request, _token):
        return "one-time-callback-code"

    monkeypatch.setattr(auth_router, "_consume_state", consume_state)
    monkeypatch.setattr(httpx.AsyncClient, "post", provider_post)
    monkeypatch.setattr(
        auth_router,
        f"verify_{provider}_id_token",
        verify_token,
    )
    monkeypatch.setattr(auth_router, "_issue_access_token", issue_access_token)
    monkeypatch.setattr(auth_router, "_save_callback_replay", save_replay)
    monkeypatch.setattr(auth_router, "_save_callback_token", save_callback_token)
    monkeypatch.setattr(auth_router.settings, "PUBLIC_SIGNUP_ENABLED", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "match_mode"),
    [
        ("google", "provider_subject"),
        ("google", "normalized_email"),
        ("microsoft", "provider_subject"),
    ],
)
async def test_oauth_callback_logs_in_existing_user_before_domain_provisioning(
    db_session,
    monkeypatch,
    provider,
    match_mode,
):
    """Launch mode must not strand users of synthetic-domain tenants."""

    tenant = Tenant(
        id=uuid.uuid4(),
        name="Operator Provisioned Firm",
        domain=f"operator-provisioned-{uuid.uuid4().hex[:8]}",
        billing_tier="intake_trial",
        is_active=True,
    )
    subject = f"{provider}-subject-123"
    provider_email = "owner@external.example"
    stored_email = (
        "previous-address@external.example"
        if match_mode == "provider_subject"
        else "Owner@External.Example"
    )
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=stored_email,
        full_name="Existing Owner",
        role="admin",
        oauth_provider=provider if match_mode == "provider_subject" else None,
        oauth_subject=subject if match_mode == "provider_subject" else None,
        is_active=True,
    )
    db_session.add_all([tenant, user])
    await db_session.commit()

    claims = {
        "sub": subject,
        "name": "Existing Owner",
        "email": provider_email,
        "preferred_username": provider_email,
        "email_verified": True,
    }
    _mock_oauth_callback(monkeypatch, provider, claims)

    async with await _oauth_client(db_session) as client:
        response = await client.get(
            f"/api/auth/{provider}/callback",
            params={"code": "provider-code", "state": "valid-state"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 307
    assert response.headers["location"].endswith(
        "/auth/callback?code=one-time-callback-code"
    )
    assert (
        await db_session.execute(select(func.count()).select_from(Tenant))
    ).scalar_one() == 1
    await db_session.refresh(user)
    assert user.tenant_id == tenant.id
    assert user.oauth_provider == provider
    assert user.oauth_subject == subject


@pytest.mark.asyncio
async def test_oauth_callback_rejects_ambiguous_cross_tenant_email_mapping(
    db_session,
    monkeypatch,
):
    provider = "google"
    shared_email = "shared@external.example"
    tenants = [
        Tenant(
            id=uuid.uuid4(),
            name=f"Firm {index}",
            domain=f"firm-{index}-{uuid.uuid4().hex[:8]}",
            billing_tier="intake_trial",
            is_active=True,
        )
        for index in range(2)
    ]
    users = [
        User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=shared_email,
            role="user",
            is_active=True,
        )
        for tenant in tenants
    ]
    db_session.add_all([*tenants, *users])
    await db_session.commit()

    claims = {
        "sub": f"unlinked-{provider}-subject",
        "name": "Ambiguous User",
        "email": shared_email,
        "preferred_username": shared_email,
        "email_verified": True,
    }
    _mock_oauth_callback(monkeypatch, provider, claims)

    async with await _oauth_client(db_session) as client:
        response = await client.get(
            f"/api/auth/{provider}/callback",
            params={"code": "provider-code", "state": "valid-state"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "multiple accounts" in response.json()["detail"]
    for user in users:
        await db_session.refresh(user)
        assert user.oauth_provider is None
        assert user.oauth_subject is None


@pytest.mark.asyncio
async def test_microsoft_same_email_cannot_claim_unlinked_existing_account(
    db_session,
    monkeypatch,
):
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Unlinked Microsoft Firm",
        domain=f"operator-provisioned-{uuid.uuid4().hex[:8]}",
        billing_tier="intake_trial",
        is_active=True,
    )
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email="owner@external.example",
        full_name="Existing Owner",
        role="admin",
        is_active=True,
    )
    db_session.add_all([tenant, user])
    await db_session.commit()

    claims = {
        "sub": "unlinked-microsoft-subject",
        "name": "Existing Owner",
        "email": user.email,
        "preferred_username": user.email,
    }
    _mock_oauth_callback(monkeypatch, "microsoft", claims)

    async with await _oauth_client(db_session) as client:
        response = await client.get(
            "/api/auth/microsoft/callback",
            params={"code": "provider-code", "state": "valid-state"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "not linked" in response.json()["detail"].lower()
    await db_session.refresh(user)
    assert user.oauth_provider is None
    assert user.oauth_subject is None
    assert (
        await db_session.execute(select(func.count()).select_from(Tenant))
    ).scalar_one() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_oauth_callback_rejects_ambiguous_provider_subject_mapping(
    db_session,
    monkeypatch,
    provider,
):
    duplicate_subject = f"duplicate-{provider}-subject"
    tenants = [
        Tenant(
            id=uuid.uuid4(),
            name=f"Subject Firm {index}",
            domain=f"subject-firm-{index}-{uuid.uuid4().hex[:8]}",
            billing_tier="intake_trial",
            is_active=True,
        )
        for index in range(2)
    ]
    users = [
        User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            email=f"subject-user-{index}@external.example",
            role="user",
            oauth_provider=provider,
            oauth_subject=duplicate_subject,
            is_active=True,
        )
        for index, tenant in enumerate(tenants)
    ]
    db_session.add_all([*tenants, *users])
    await db_session.commit()

    claims = {
        "sub": duplicate_subject,
        "name": "Duplicate Subject",
        "email": "new-address@external.example",
        "preferred_username": "new-address@external.example",
        "email_verified": True,
    }
    _mock_oauth_callback(monkeypatch, provider, claims)

    async with await _oauth_client(db_session) as client:
        response = await client.get(
            f"/api/auth/{provider}/callback",
            params={"code": "provider-code", "state": "valid-state"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "multiple accounts" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_oauth_callback_cannot_provision_in_launch_mode(
    db_session,
    monkeypatch,
    provider,
):
    provider_email = "unapproved@new-tenant.example"
    claims = {
        "sub": f"new-{provider}-subject",
        "name": "Unapproved User",
        "email": provider_email,
        "preferred_username": provider_email,
        "email_verified": True,
    }
    _mock_oauth_callback(monkeypatch, provider, claims)

    async with await _oauth_client(db_session) as client:
        response = await client.get(
            f"/api/auth/{provider}/callback",
            params={"code": "provider-code", "state": "valid-state"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 403
    detail = response.json()["detail"].lower()
    if provider == "microsoft":
        assert "not linked" in detail
    else:
        assert "public signup is not enabled" in detail
    assert (
        await db_session.execute(select(func.count()).select_from(Tenant))
    ).scalar_one() == 0
    assert (
        await db_session.execute(select(func.count()).select_from(User))
    ).scalar_one() == 0
