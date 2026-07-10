from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import tenant_oauth_apps


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _Db:
    def __init__(self, row):
        self._row = row

    async def execute(self, _stmt):
        return _Result(self._row)


@pytest.mark.asyncio
async def test_zoom_phone_oauth_client_prefers_tenant_app(monkeypatch):
    app = SimpleNamespace(
        encrypted_client_id="encrypted-client-id",
        encrypted_client_secret="encrypted-client-secret",
        zoom_account_id="zoom-account-1",
    )
    secrets = {
        "encrypted-client-id": "tenant-client",
        "encrypted-client-secret": "tenant-secret",
    }
    monkeypatch.setattr(
        tenant_oauth_apps, "decrypt_token", lambda value: secrets[value]
    )
    monkeypatch.setattr(tenant_oauth_apps.settings, "ZOOM_CLIENT_ID", "platform-client")
    monkeypatch.setattr(
        tenant_oauth_apps.settings, "ZOOM_CLIENT_SECRET", "platform-secret"
    )

    client = await tenant_oauth_apps.get_zoom_phone_oauth_client(
        _Db(app),
        tenant_id=uuid4(),
    )

    assert client.source == "tenant"
    assert client.client_id == "tenant-client"
    assert client.client_secret == "tenant-secret"
    assert client.account_id == "zoom-account-1"


@pytest.mark.asyncio
async def test_zoom_phone_oauth_client_never_uses_platform_app(monkeypatch):
    monkeypatch.setattr(tenant_oauth_apps.settings, "ZOOM_CLIENT_ID", "platform-client")
    monkeypatch.setattr(
        tenant_oauth_apps.settings, "ZOOM_CLIENT_SECRET", "platform-secret"
    )

    client = await tenant_oauth_apps.get_zoom_phone_oauth_client(
        _Db(None),
        tenant_id=uuid4(),
    )

    assert client is None


@pytest.mark.asyncio
async def test_zoom_phone_oauth_client_requires_explicit_account_mapping():
    app = SimpleNamespace(
        encrypted_client_id="encrypted-client-id",
        encrypted_client_secret="encrypted-client-secret",
        zoom_account_id=None,
    )

    client = await tenant_oauth_apps.get_zoom_phone_oauth_client(
        _Db(app),
        tenant_id=uuid4(),
    )

    assert client is None
