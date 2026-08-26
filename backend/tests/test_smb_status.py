"""Regression tests for the tenant-scoped SMB status contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

import app.routers.cloud_admin as cloud_admin
import app.config as config_module
from app.services import smb as smb_module
from app.services.smb import SmbService


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_smb_status_returns_inventory_when_retrieval_is_disabled(
    monkeypatch, enabled
):
    db = object()
    admin = SimpleNamespace(tenant_id="tenant-1")
    stats = {
        "agent_count": 1,
        "active_agents": 1,
        "share_count": 1,
        "file_count": 7,
        "credential_count": 1,
        "shares_failing": 0,
        "shares_without_credential": 0,
    }
    calls = []

    async def fake_require_admin(request, session):
        assert session is db
        return admin

    async def fake_set_tenant_context(session, tenant_id):
        calls.append((session, tenant_id))

    class FakeSmbService:
        async def get_admin_stats(self, session, tenant_id):
            assert session is db
            assert tenant_id == "tenant-1"
            return dict(stats)

    monkeypatch.setattr(cloud_admin, "_require_admin", fake_require_admin)
    monkeypatch.setattr(cloud_admin, "set_tenant_context", fake_set_tenant_context)
    # The endpoint imports the singleton inside the function, so patch the
    # service module object that import resolves to.
    monkeypatch.setattr(smb_module, "smb_service", FakeSmbService())
    monkeypatch.setattr(
        config_module, "get_settings", lambda: SimpleNamespace(SMB_ENABLED=enabled)
    )

    result = await cloud_admin.smb_status(SimpleNamespace(), db)

    assert result["agent_count"] == 1
    assert result["file_count"] == 7
    assert result["enabled"] is enabled
    assert result["retrieval_enabled"] is enabled
    assert calls == [(db, "tenant-1")]
    if enabled:
        assert "message" not in result
    else:
        assert "disabled by server configuration" in result["message"]


@pytest.mark.asyncio
async def test_admin_stats_include_latest_operational_timestamps(monkeypatch):
    heartbeat = "2026-08-26T15:00:00Z"
    scan = "2026-08-26T15:01:00Z"
    values = [1, 1, 1, 7, 1234, 0, 1, 0, 0, heartbeat, scan]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_ScalarResult(value) for value in values])
    )
    monkeypatch.setattr(smb_module, "set_tenant_context", AsyncMock())

    result = await SmbService().get_admin_stats(
        db, "00000000-0000-0000-0000-000000000001"
    )

    assert result["agent_count"] == 1
    assert result["file_count"] == 7
    assert result["last_agent_heartbeat"] == heartbeat
    assert result["last_file_sync"] == scan
    active_agents_query = str(
        db.execute.await_args_list[1]
        .args[0]
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "smb_agents.api_key_hash != 'pending'" in active_agents_query
