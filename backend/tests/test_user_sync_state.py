import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.tenant_credential import TenantCredential
from app.models.user import User
from app.services.user_sync import UserSyncService


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Async-context-manager stand-in for httpx.AsyncClient."""

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, params=None):
        return _FakeResp(200, self._payload)


@pytest.mark.asyncio
async def test_tenant_credential_has_sync_state_columns(db_session, test_tenant):
    cred = TenantCredential(
        tenant_id=test_tenant.id,
        provider="google",
        encrypted_access_token="enc",
        scopes="https://www.googleapis.com/auth/admin.directory.user.readonly",
        is_active=True,
        last_user_sync_total=3,
        last_user_sync_created=2,
        last_user_sync_updated=1,
        last_user_sync_status="ok",
    )
    db_session.add(cred)
    await db_session.commit()

    row = (
        await db_session.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == test_tenant.id,
                TenantCredential.provider == "google",
            )
        )
    ).scalar_one()
    assert row.last_user_sync_total == 3
    assert row.last_user_sync_status == "ok"
    assert row.last_user_sync_at is None
    assert row.last_user_sync_error is None


@pytest.mark.asyncio
async def test_ms_sync_creates_free_tier_user_and_records_state(
    db_session, test_tenant
):
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="microsoft",
            encrypted_access_token="enc",
            scopes="User.Read.All",
            is_active=True,
        )
    )
    await db_session.commit()

    payload = {
        "value": [
            {"id": "ms-1", "mail": "new.hire@testfirm.com", "displayName": "New Hire"}
        ]
    }
    with (
        patch(
            "app.services.user_sync.get_fresh_token", new=AsyncMock(return_value="tok")
        ),
        patch(
            "app.services.user_sync.httpx.AsyncClient",
            return_value=_FakeClient(payload),
        ),
    ):
        res = await UserSyncService().sync_microsoft_users(
            db_session, str(test_tenant.id)
        )

    assert res["created"] == 1
    user = (
        await db_session.execute(
            select(User).where(User.email == "new.hire@testfirm.com")
        )
    ).scalar_one()
    # Regression B: synced users land on free tier
    assert user.license_active is False

    cred = (
        await db_session.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == test_tenant.id,
                TenantCredential.provider == "microsoft",
            )
        )
    ).scalar_one()
    assert cred.last_user_sync_status == "ok"
    assert cred.last_user_sync_total == 1
    assert cred.last_user_sync_at is not None


@pytest.mark.asyncio
async def test_sync_does_not_relicense_existing_user(db_session, test_tenant):
    # Existing licensed user (e.g. firm owner) already in the directory result
    db_session.add(
        User(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            email="owner@testfirm.com",
            full_name="Owner",
            role="admin",
            is_active=True,
            license_active=True,
        )
    )
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="microsoft",
            encrypted_access_token="enc",
            scopes="User.Read.All",
            is_active=True,
        )
    )
    await db_session.commit()

    payload = {
        "value": [{"id": "ms-9", "mail": "owner@testfirm.com", "displayName": "Owner"}]
    }
    with (
        patch(
            "app.services.user_sync.get_fresh_token", new=AsyncMock(return_value="tok")
        ),
        patch(
            "app.services.user_sync.httpx.AsyncClient",
            return_value=_FakeClient(payload),
        ),
    ):
        await UserSyncService().sync_microsoft_users(db_session, str(test_tenant.id))

    owner = (
        await db_session.execute(select(User).where(User.email == "owner@testfirm.com"))
    ).scalar_one()
    # Regression B: existing license untouched by sync
    assert owner.license_active is True


@pytest.mark.asyncio
async def test_permissions_returns_user_count_and_freshness(
    client, db_session, test_tenant, test_user
):
    # test_user has oauth_provider="google"; add a connected google credential
    db_session.add(
        TenantCredential(
            tenant_id=test_tenant.id,
            provider="google",
            encrypted_access_token="enc",
            scopes="https://www.googleapis.com/auth/admin.directory.user.readonly",
            is_active=True,
            last_user_sync_total=5,
            last_user_sync_status="ok",
        )
    )
    await db_session.commit()

    resp = await client.get("/api/admin/permissions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["google"]["user_count"] >= 1
    assert data["google"]["last_sync_total"] == 5
    assert data["google"]["last_sync_status"] == "ok"
