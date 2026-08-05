import sys
import types
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.llm_provider_key import LLMProviderKey
from app.models.operator_audit import OperatorAuditLog
from app.routers import platform_llm as platform_llm_router
from tests.platform_auth_helpers import platform_headers

TEST_PLATFORM_KEY = "test-platform-key-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


async def _add_provider_key(db_session, *, provider_id: str = "openrouter"):
    key = LLMProviderKey(
        id=uuid.uuid4(),
        name="Audit test key",
        provider_id=provider_id,
        encrypted_key="encrypted",
        key_hint="hint",
    )
    db_session.add(key)
    await db_session.commit()
    return key


async def _audit_actions(db_session):
    result = await db_session.execute(select(OperatorAuditLog))
    return [row for row in result.scalars().all() if row.action != "platform.request"]


@pytest.mark.asyncio
async def test_route_save_records_operator_audit(
    client: AsyncClient, db_session, monkeypatch
):
    key = await _add_provider_key(db_session)
    reload_call = {}

    async def fake_reload(config, keys_by_id, *, aliases=None, validate=True):
        reload_call.update(
            config=config,
            keys_by_id=keys_by_id,
            aliases=aliases,
            validate=validate,
        )
        return {"litellm_updated": True, "litellm_error": None}

    async def fake_gateway_status(aliases=None):
        return {"status": "disabled"}

    monkeypatch.setattr(platform_llm_router, "_reload_litellm_routes", fake_reload)
    monkeypatch.setattr(
        platform_llm_router, "_check_litellm_gateway", fake_gateway_status
    )

    response = await client.put(
        "/api/platform/llm/routes",
        headers=platform_headers(),
        json={
            "standard": {
                "provider_id": key.provider_id,
                "key_id": str(key.id),
                "model": "google/gemma-4-31b-it:free",
                "capacity": 100,
                "alternates": [],
                "fallbacks": [],
            },
            "premium": {
                "provider_id": key.provider_id,
                "key_id": str(key.id),
                "model": "openai/gpt-4.1",
                "capacity": 100,
                "alternates": [],
                "fallbacks": [],
            },
        },
    )

    assert response.status_code == 200
    assert reload_call["keys_by_id"] == {str(key.id): key}
    assert reload_call["aliases"] == platform_llm_router._managed_route_aliases(
        reload_call["config"]
    )
    assert reload_call["validate"] is True
    logs = await _audit_actions(db_session)
    assert [log.action for log in logs] == ["llm.routes_saved"]
    assert logs[0].metadata_json["standard"]["model"] == "google/gemma-4-31b-it:free"
    assert "api_key" not in str(logs[0].metadata_json)


@pytest.mark.asyncio
async def test_provider_key_delete_records_provider_disable_audit(
    client: AsyncClient, db_session
):
    key = await _add_provider_key(db_session, provider_id="opencode-zen")

    response = await client.delete(
        f"/api/platform/llm/provider-keys/{key.id}",
        headers=platform_headers(),
    )

    assert response.status_code == 200
    logs = await _audit_actions(db_session)
    assert [log.action for log in logs] == ["llm.provider_disabled"]
    assert logs[0].resource_type == "llm_provider_key"
    assert logs[0].resource_id == str(key.id)
    assert logs[0].metadata_json == {
        "provider_id": "opencode-zen",
        "key_name": "Audit test key",
        "key_hint": "hint",
    }


@pytest.mark.asyncio
async def test_model_test_records_operator_audit(
    client: AsyncClient, db_session, monkeypatch
):
    key = await _add_provider_key(db_session, provider_id="openrouter")
    monkeypatch.setattr(platform_llm_router, "decrypt_token", lambda value: "sk-test")

    class _Usage:
        prompt_tokens = 5
        completion_tokens = 1
        total_tokens = 6

    class _Message:
        content = "OK"

    class _Choice:
        message = _Message()

    class _Completion:
        model = "google/gemma-4-31b-it:free"
        choices = [_Choice()]
        usage = _Usage()

    class _ChatCompletions:
        async def create(self, **kwargs):
            return _Completion()

    class _Chat:
        completions = _ChatCompletions()

    class _AsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    monkeypatch.setitem(
        sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_AsyncOpenAI)
    )

    response = await client.post(
        "/api/platform/llm/routes/test",
        headers=platform_headers(),
        json={
            "provider_id": "openrouter",
            "key_id": str(key.id),
            "model": "google/gemma-4-31b-it:free",
            "route": "standard",
        },
    )

    assert response.status_code == 200
    logs = await _audit_actions(db_session)
    assert [log.action for log in logs] == ["llm.model_tested"]
    assert logs[0].metadata_json["provider_id"] == "openrouter"
    assert logs[0].metadata_json["model"] == "google/gemma-4-31b-it:free"
    assert logs[0].metadata_json["ok"] is True
    assert "Reply with exactly" not in str(logs[0].metadata_json)


def test_debug_mode_audit_payload_is_metadata_only():
    payload = platform_llm_router.operator_debug_mode_audit_payload(
        tenant_id="tenant-123",
        conversation_id="conv-456",
        enabled=True,
        retention_days=7,
        reason="support request",
        prompt="privileged facts must not be logged",
    )

    assert payload == {
        "tenant_id": "tenant-123",
        "conversation_id": "conv-456",
        "enabled": True,
        "retention_days": 7,
        "reason": "support request",
    }
