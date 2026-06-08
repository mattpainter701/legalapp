import pytest
from httpx import AsyncClient

from app.routers import platform as platform_router
from app.routers import platform_llm as platform_llm_router
from app.models.llm_provider_key import LLMProviderKey


@pytest.mark.asyncio
async def test_platform_llm_config_round_trip(client: AsyncClient):
    platform_router.settings.PLATFORM_SECRET_KEY = "test-platform-key"
    headers = {"X-Platform-Key": "test-platform-key"}

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


@pytest.mark.asyncio
async def test_provider_route_builder_rejects_mismatched_key_provider(
    client: AsyncClient, db_session
):
    platform_llm_router.settings.PLATFORM_SECRET_KEY = "test-platform-key"
    headers = {"X-Platform-Key": "test-platform-key"}

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
    platform_llm_router.settings.PLATFORM_SECRET_KEY = "test-platform-key"
    headers = {"X-Platform-Key": "test-platform-key"}

    resp = await client.delete(
        "/api/platform/llm/provider-keys/not-a-uuid",
        headers=headers,
    )
    assert resp.status_code == 400
