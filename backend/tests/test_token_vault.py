import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.tenant_credential import TenantCredential
from app.services import token_vault


@pytest.fixture(autouse=True)
def token_key(monkeypatch):
    monkeypatch.setattr(
        token_vault.settings,
        "TOKEN_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
        raising=False,
    )


def _expired_credential(test_tenant, provider="microsoft"):
    return TenantCredential(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        provider=provider,
        encrypted_access_token=token_vault.encrypt_token("old-access"),
        encrypted_refresh_token=token_vault.encrypt_token("old-refresh"),
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        health="healthy",
        is_active=True,
    )


class _TokenRow:
    def __init__(self):
        self.encrypted_access_token = token_vault.encrypt_token("old-access")
        self.encrypted_refresh_token = token_vault.encrypt_token("old-refresh")
        self.token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.health = "healthy"
        self.missing_scopes = None
        self.last_refresh_at = None
        self.last_refresh_error = None
        self.is_active = True


class _FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_refresh_retries_transient_failure_and_persists_rotation(
    monkeypatch,
):
    row = _TokenRow()
    db = _FakeDb()

    calls = []

    async def fake_post(self, url, *args, **kwargs):
        calls.append(kwargs["data"]["refresh_token"])
        if len(calls) == 1:
            return httpx.Response(
                500,
                text="temporary",
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
            request=httpx.Request("POST", url),
        )

    async def no_sleep(delay):
        return None

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(token_vault.asyncio, "sleep", no_sleep)

    result = await token_vault._refresh_locked_row(db, row, "microsoft")

    assert result == ("new-access", 3600)
    assert calls == ["old-refresh", "old-refresh"]
    assert token_vault.decrypt_token(row.encrypted_access_token) == "new-access"
    assert token_vault.decrypt_token(row.encrypted_refresh_token) == "new-refresh"
    assert row.health == "healthy"
    assert row.last_refresh_error is None
    assert row.last_refresh_at is not None
    assert db.commits == 1


@pytest.mark.asyncio
async def test_invalid_grant_marks_tenant_credential_revoked(
    monkeypatch,
):
    row = _TokenRow()
    db = _FakeDb()

    async def fake_post(self, url, *args, **kwargs):
        return httpx.Response(
            400,
            json={"error": "invalid_grant"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await token_vault._refresh_locked_row(db, row, "google")

    assert result is None
    assert row.health == "revoked"
    assert row.is_active is False
    assert "invalid_grant" in row.last_refresh_error
    assert row.last_refresh_at is not None
    assert db.commits == 1


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires a reachable Postgres TEST_DATABASE_URL for row-lock semantics",
)
async def test_concurrent_get_fresh_token_single_flights_refresh(
    test_engine, db_session, test_tenant, monkeypatch
):
    cred = _expired_credential(test_tenant, "microsoft")
    db_session.add(cred)
    await db_session.commit()

    calls = 0

    async def fake_post(self, url, *args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.15)
        return httpx.Response(
            200,
            json={
                "access_token": "shared-access",
                "refresh_token": "shared-refresh",
                "expires_in": 3600,
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def load_token():
        async with factory() as session:
            return await token_vault.get_fresh_token(
                session, str(test_tenant.id), "microsoft"
            )

    tokens = await asyncio.gather(load_token(), load_token())

    assert tokens == ["shared-access", "shared-access"]
    assert calls == 1
