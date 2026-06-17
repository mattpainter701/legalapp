import uuid

import pytest
from httpx import AsyncClient

from app.routers import platform as platform_router
from app.routers import platform_llm as platform_llm_router
from app.models.llm_provider_key import LLMProviderKey

TEST_PLATFORM_KEY = "test-platform-key-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


@pytest.mark.asyncio
async def test_platform_llm_config_round_trip(client: AsyncClient):
    platform_router.settings.PLATFORM_SECRET_KEY = TEST_PLATFORM_KEY
    headers = {"X-Platform-Key": TEST_PLATFORM_KEY}

    update = await client.put(
        "/api/platform/llm-config",
        headers=headers,
        json={
            "standard_provider": "litellm",
            "standard_model": "clarity-standard",
            "premium_provider": "litellm",
            "premium_model": "clarity-premium-openrouter",
        },
    )
    assert update.status_code == 200
    assert update.json()["config"]["premium_model"] == "clarity-premium-openrouter"

    get_resp = await client.get("/api/platform/llm-config", headers=headers)
    assert get_resp.status_code == 200
    config = get_resp.json()["config"]
    assert config["standard_provider"] == "litellm"
    assert config["premium_provider"] == "litellm"


def test_provider_route_builder_litellm_model_prefixes():
    opencode = platform_llm_router._build_litellm_model_entry(
        "clarity-standard", "opencode-zen", "deepseek-v4-flash-free", "sk-test"
    )
    assert opencode["litellm_params"]["model"] == "openai/deepseek-v4-flash-free"
    assert opencode["litellm_params"]["api_base"] == "https://opencode.ai/zen/v1"

    openrouter = platform_llm_router._build_litellm_model_entry(
        "clarity-standard-fb-0",
        "openrouter",
        "qwen/qwen3-235b-a22b:free",
        "sk-test",
    )
    assert openrouter["litellm_params"]["model"] == "openrouter/qwen/qwen3-235b-a22b:free"
    assert "api_base" not in openrouter["litellm_params"]

    anthropic = platform_llm_router._build_litellm_model_entry(
        "clarity-premium", "anthropic", "claude-3-5-sonnet-latest", "sk-test"
    )
    assert anthropic["litellm_params"]["model"] == "anthropic/claude-3-5-sonnet-latest"
    assert "api_base" not in anthropic["litellm_params"]


def test_model_catalog_capabilities_from_provider_metadata():
    model = platform_llm_router._normalize_model_item(
        {
            "id": "google/gemma-4-26b-a4b-it:free",
            "description": "Instruction-tuned multimodal model with structured output.",
            "context_length": 262144,
            "architecture": {"modality": "text+image->text"},
            "supported_parameters": ["tools", "response_format"],
            "pricing": {"prompt": "0", "completion": "0"},
        },
        "openrouter",
    )

    assert model["is_free"] is True
    assert set(model["capabilities"]).issuperset(
        {"vision", "instruction", "tool_use", "structured_output", "large_context", "rag"}
    )


def test_litellm_reload_payload_builds_aliases_and_reports_stale_targets(monkeypatch):
    openrouter_key = LLMProviderKey(
        id=uuid.uuid4(),
        name="OpenRouter",
        provider_id="openrouter",
        encrypted_key="encrypted-openrouter",
        key_hint="test",
    )
    opencode_key = LLMProviderKey(
        id=uuid.uuid4(),
        name="OpenCode",
        provider_id="opencode-zen",
        encrypted_key="encrypted-opencode",
        key_hint="test",
    )
    monkeypatch.setattr(
        platform_llm_router,
        "decrypt_token",
        lambda encrypted: f"plain-{encrypted}",
    )

    models, fallbacks, errors = platform_llm_router._build_litellm_reload_payload(
        {
            "standard": {
                "key_id": str(openrouter_key.id),
                "provider_id": "openrouter",
                "model": "qwen/qwen3-235b-a22b:free",
                "capacity": 80,
                "alternates": [
                    {
                        "key_id": str(opencode_key.id),
                        "provider_id": "opencode-zen",
                        "model": "deepseek-v4-flash-free",
                        "capacity": 20,
                    }
                ],
                "fallbacks": [
                    {
                        "key_id": str(openrouter_key.id),
                        "provider_id": "deepseek",
                        "model": "deepseek-chat",
                    }
                ],
            },
            "premium": {},
        },
        {str(openrouter_key.id): openrouter_key, str(opencode_key.id): opencode_key},
    )

    assert [model["model_name"] for model in models] == [
        "clarity-standard",
        "clarity-standard",
    ]
    assert models[0]["litellm_params"]["model"] == "openrouter/qwen/qwen3-235b-a22b:free"
    assert models[0]["litellm_params"]["weight"] == 80
    assert models[1]["litellm_params"]["model"] == "openai/deepseek-v4-flash-free"
    assert fallbacks == []
    assert errors == ["standard fallback 1: selected key belongs to openrouter, not deepseek"]


@pytest.mark.asyncio
async def test_litellm_reload_routes_reports_empty_config():
    result = await platform_llm_router._reload_litellm_routes(
        {"standard": {}, "premium": {}}, {}
    )

    assert result["litellm_updated"] is False
    assert result["models_registered"] == 0
    assert result["fallbacks_registered"] == 0
    assert "No complete provider/key/model targets" in result["litellm_error"]


@pytest.mark.asyncio
async def test_provider_route_builder_rejects_mismatched_key_provider(
    client: AsyncClient, db_session
):
    platform_llm_router.settings.PLATFORM_SECRET_KEY = TEST_PLATFORM_KEY
    headers = {"X-Platform-Key": TEST_PLATFORM_KEY}

    key = LLMProviderKey(
        name="DeepSeek test key",
        provider_id="deepseek",
        encrypted_key="unused",
        key_hint="test",
    )
    db_session.add(key)
    await db_session.commit()
    await db_session.refresh(key)

    resp = await client.put(
        "/api/platform/llm/routes",
        headers=headers,
        json={
            "standard": {
                "provider_id": "openrouter",
                "key_id": str(key.id),
                "model": "qwen/qwen3-235b-a22b:free",
                "fallbacks": [],
            },
            "premium": {},
        },
    )
    assert resp.status_code == 400
    assert "selected key belongs to deepseek" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_provider_key_invalid_uuid_returns_400(client: AsyncClient):
    platform_llm_router.settings.PLATFORM_SECRET_KEY = TEST_PLATFORM_KEY
    headers = {"X-Platform-Key": TEST_PLATFORM_KEY}

    resp = await client.delete(
        "/api/platform/llm/provider-keys/not-a-uuid",
        headers=headers,
    )
    assert resp.status_code == 400
