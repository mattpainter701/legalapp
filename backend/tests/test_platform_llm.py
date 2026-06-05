import pytest
from httpx import AsyncClient

from app.routers import platform as platform_router


@pytest.mark.asyncio
async def test_platform_llm_config_round_trip(client: AsyncClient):
    platform_router.settings.PLATFORM_SECRET_KEY = "test-platform-key"
    headers = {"X-Platform-Key": "test-platform-key"}

    update = await client.put(
        "/api/platform/llm-config",
        headers=headers,
        json={
            "standard_provider": "deepseek",
            "standard_model": "deepseek-chat",
            "premium_provider": "openrouter",
            "premium_model": "anthropic/claude-sonnet-4",
        },
    )
    assert update.status_code == 200
    assert update.json()["config"]["premium_model"] == "anthropic/claude-sonnet-4"

    get_resp = await client.get("/api/platform/llm-config", headers=headers)
    assert get_resp.status_code == 200
    config = get_resp.json()["config"]
    assert config["standard_provider"] == "deepseek"
    assert config["premium_provider"] == "openrouter"
