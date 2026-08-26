from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.routers import smb as smb_router
from app.schemas.smb import AgentRegisterRequest, AgentRegisterResponse
from app.services import token_vault


class _MemoryRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.expirations[key] = ex


def _body(agent_name="File Server 01"):
    return AgentRegisterRequest(
        pairing_code="ABCD-EFGH-IJKL",
        agent_name=agent_name,
        agent_version="0.14.0",
        hostname="fs01",
        os_info="Windows Server 2022",
    )


@pytest.mark.asyncio
async def test_registration_response_can_be_replayed_after_a_lost_response(monkeypatch):
    monkeypatch.setattr(token_vault.settings, "TOKEN_ENCRYPTION_KEYS", "")
    monkeypatch.setattr(
        token_vault.settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    redis = _MemoryRedis()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
    db = SimpleNamespace(commit=AsyncMock())
    response = AgentRegisterResponse(agent_id="agent-id", api_key="raw-secret")
    register = AsyncMock(return_value=response)
    monkeypatch.setattr(smb_router.smb_service, "register_agent", register)

    first = await smb_router.register_agent(_body(), request, db)
    second = await smb_router.register_agent(_body(), request, db)

    assert first == second == response
    register.assert_awaited_once()
    db.commit.assert_awaited_once()
    key = next(iter(redis.values))
    assert "ABCD-EFGH-IJKL" not in key
    assert redis.expirations[key] == 120
    assert "raw-secret" not in redis.values[key]


@pytest.mark.asyncio
async def test_registration_receipt_is_bound_to_connector_identity(monkeypatch):
    monkeypatch.setattr(token_vault.settings, "TOKEN_ENCRYPTION_KEYS", "")
    monkeypatch.setattr(
        token_vault.settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    redis = _MemoryRedis()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
    db = SimpleNamespace(commit=AsyncMock())
    response = AgentRegisterResponse(agent_id="agent-id", api_key="raw-secret")
    register = AsyncMock(side_effect=[response, ValueError("Invalid pairing code")])
    monkeypatch.setattr(smb_router.smb_service, "register_agent", register)

    await smb_router.register_agent(_body(), request, db)
    with pytest.raises(HTTPException) as exc_info:
        await smb_router.register_agent(_body(agent_name="Impostor"), request, db)

    assert exc_info.value.status_code == 400
    assert register.await_count == 2


@pytest.mark.asyncio
async def test_corrupt_registration_receipt_is_never_treated_as_a_credential(
    monkeypatch,
):
    monkeypatch.setattr(token_vault.settings, "TOKEN_ENCRYPTION_KEYS", "")
    monkeypatch.setattr(
        token_vault.settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    redis = _MemoryRedis()
    redis.values[smb_router._registration_receipt_key(_body().pairing_code)] = (
        "not-a-fernet-token"
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
    db = SimpleNamespace(commit=AsyncMock())
    response = AgentRegisterResponse(agent_id="agent-id", api_key="fresh-secret")
    register = AsyncMock(return_value=response)
    monkeypatch.setattr(smb_router.smb_service, "register_agent", register)

    result = await smb_router.register_agent(_body(), request, db)

    assert result == response
    register.assert_awaited_once()


@pytest.mark.asyncio
async def test_receipt_encryption_failure_does_not_hide_committed_api_key(monkeypatch):
    monkeypatch.setattr(token_vault.settings, "TOKEN_ENCRYPTION_KEYS", "")
    monkeypatch.setattr(token_vault.settings, "TOKEN_ENCRYPTION_KEY", "invalid")
    redis = _MemoryRedis()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
    db = SimpleNamespace(commit=AsyncMock())
    response = AgentRegisterResponse(agent_id="agent-id", api_key="fresh-secret")
    register = AsyncMock(return_value=response)
    monkeypatch.setattr(smb_router.smb_service, "register_agent", register)

    result = await smb_router.register_agent(_body(), request, db)

    assert result == response
    db.commit.assert_awaited_once()
    assert redis.values == {}
