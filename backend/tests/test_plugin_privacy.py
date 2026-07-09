from types import SimpleNamespace

import pytest

from app.services.plugins.executor import PluginExecutor


class RecordingLLM:
    def __init__(self):
        self.kwargs = None

    async def complete(self, **kwargs):
        self.kwargs = kwargs
        return "Draft response. [settled]", 10, 5


@pytest.mark.asyncio
async def test_plugin_executor_scrubs_every_provider_bound_text(monkeypatch):
    llm = RecordingLLM()
    executor = PluginExecutor(llm)

    async def fake_profile(*args, **kwargs):
        return "Contact jane@example.com or 701-555-1212"

    async def fake_route(*args, **kwargs):
        return SimpleNamespace(
            provider="litellm",
            model="test-model",
            requested_route="standard",
            resolved_route="standard",
            gateway_provider="litellm",
            gateway_alias="standard",
        )

    monkeypatch.setattr(executor, "get_practice_profile", fake_profile)
    monkeypatch.setattr("app.services.plugins.executor.resolve_llm_route", fake_route)

    result = await executor.execute(
        db=SimpleNamespace(),
        plugin="test-plugin",
        skill="analysis",
        input_text="SSN 123-45-6789; email jane@example.com",
        tenant_id="tenant-id",
        user_id="user-id",
        context={
            "tenant_name": "Firm jane@example.com",
            "matter_context": "Call 701-555-1212",
        },
        privacy_mode=True,
    )

    provider_payload = str(llm.kwargs)
    assert "123-45-6789" not in provider_payload
    assert "jane@example.com" not in provider_payload
    assert "701-555-1212" not in provider_payload
    # No verified sources were supplied, so confidence cannot remain settled.
    assert result["memo"].endswith("[verify]")
