from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from openai import APIConnectionError

from app.config import get_settings
from app.services.llm import LLMService
from app.services.llm_routing import _current_managed_alias


settings = get_settings()


def test_tenant_managed_alias_follows_active_route_revision():
    config = {
        "standard_model": "clarity-standard-rnew",
        "premium_model": "clarity-premium-rnew",
    }

    assert (
        _current_managed_alias("clarity-standard-rold", config)
        == "clarity-standard-rnew"
    )
    assert (
        _current_managed_alias("custom-tenant-alias", config) == "custom-tenant-alias"
    )


def test_disclaimer_footer_is_conditional_for_legal_work_only():
    prompt = LLMService()._build_system_prompt(
        tenant_name="Bismarcklaw",
        context="",
        memory_context="",
        user_name="Matt",
    )

    assert (
        "Prepared for Bismarcklaw. Attorney review recommended before reliance."
        in prompt
    )
    assert "only when the response contains legal analysis" in prompt
    assert "Do not append that footer to ordinary non-legal answers" in prompt
    assert "End every response with" not in prompt


def test_system_prompt_does_not_expose_firm_context_label():
    prompt = LLMService()._build_system_prompt(
        tenant_name="Bismarcklaw",
        context="State v. Robertson",
        memory_context="",
        user_name="Matt",
    )

    assert "FIRM CONTEXT" not in prompt
    assert "SOURCE MATERIALS" in prompt
    assert "Never write the phrase" in prompt


def test_gateway_delegates_fallbacks_to_litellm():
    service = LLMService()
    with patch(
        "app.services.llm_routing.is_model_in_cooldown",
        side_effect=lambda alias: alias == settings.LITELLM_STANDARD_MODEL,
    ):
        candidates = service._gateway_candidates(
            settings.LITELLM_STANDARD_MODEL,
            use_premium=False,
            customer_api_key=None,
        )

    assert candidates == [settings.LITELLM_STANDARD_MODEL]


@pytest.mark.asyncio
async def test_standard_stream_does_not_bypass_litellm_route_graph():
    class FakeStream:
        def __init__(self, chunks):
            self._chunks = iter(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                content = next(self._chunks)
            except StopIteration:
                raise StopAsyncIteration
            return SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
            )

    class FakeCompletions:
        def __init__(self):
            self.models = []

        async def create(self, **kwargs):
            self.models.append(kwargs["model"])
            raise APIConnectionError(
                request=httpx.Request("POST", "http://litellm.local")
            )

    async def run():
        service = LLMService()
        completions = FakeCompletions()
        service.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with pytest.raises(RuntimeError, match="LiteLLM Gateway API error"):
            _ = [
                chunk
                async for chunk in service.stream_complete(
                    [{"role": "user", "content": "test"}],
                    tenant_name="Tenant",
                    context="",
                    model=settings.LITELLM_STANDARD_MODEL,
                )
            ]
        return completions.models

    models = await run()

    assert models == [settings.LITELLM_STANDARD_MODEL]


@pytest.mark.asyncio
async def test_complete_sends_metadata_without_prompt_content():
    class FakeCompletions:
        def __init__(self):
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
                model="openrouter/provider-actual-model",
            )

    async def run():
        service = LLMService()
        completions = FakeCompletions()
        usage = {}
        service.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        await service.complete(
            [{"role": "user", "content": "privileged prompt"}],
            tenant_name="Tenant",
            context="confidential context",
            model=settings.LITELLM_STANDARD_MODEL,
            gateway_metadata={
                "tenant_id": "tenant-1",
                "operation_type": "chat",
                "prompt": "privileged prompt",
            },
            usage_sink=usage,
        )
        return completions.kwargs, usage

    kwargs, usage = await run()

    assert kwargs["extra_body"] == {
        "metadata": {"tenant_id": "tenant-1", "operation_type": "chat"}
    }
    assert usage == {
        "requested_model": settings.LITELLM_STANDARD_MODEL,
        "model": "openrouter/provider-actual-model",
        "tokens_in": 3,
        "tokens_out": 2,
    }


@pytest.mark.asyncio
async def test_customer_byok_complete_does_not_send_gateway_metadata():
    class FakeCompletions:
        def __init__(self):
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

    async def run():
        service = LLMService()
        completions = FakeCompletions()
        service._client_for = lambda *args, **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        await service.complete(
            [{"role": "user", "content": "tenant byok prompt"}],
            tenant_name="Tenant",
            context="confidential context",
            model="customer-model",
            customer_api_key="tenant-key",
            customer_provider="gemini",
            gateway_metadata={
                "tenant_id": "tenant-1",
                "operation_type": "chat",
            },
        )
        return completions.kwargs

    kwargs = await run()

    assert "extra_body" not in kwargs
