import asyncio
from types import SimpleNamespace

import httpx
from openai import APIConnectionError

from app.config import get_settings
from app.services.llm import LLMService


settings = get_settings()


def test_disclaimer_footer_is_conditional_for_legal_work_only():
    prompt = LLMService()._build_system_prompt(
        tenant_name="Bismarcklaw",
        context="",
        memory_context="",
        user_name="Matt",
    )

    assert "Prepared for Bismarcklaw. Attorney review recommended before reliance." in prompt
    assert "only when the response contains legal analysis" in prompt
    assert "Do not append that footer to ordinary non-legal answers" in prompt
    assert "End every response with" not in prompt


def test_standard_stream_retries_gateway_fallback_before_first_token():
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
            if len(self.models) == 1:
                raise APIConnectionError(
                    request=httpx.Request("POST", "http://litellm.local")
                )
            return FakeStream(["fallback ", "answer"])

    async def run():
        service = LLMService()
        completions = FakeCompletions()
        service.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        chunks = [
            chunk
            async for chunk in service.stream_complete(
                [{"role": "user", "content": "test"}],
                tenant_name="Tenant",
                context="",
                model=settings.LITELLM_STANDARD_MODEL,
            )
        ]
        return completions.models, chunks

    models, chunks = asyncio.run(run())

    assert models == [
        settings.LITELLM_STANDARD_MODEL,
        "clarity-standard-fb-0",
    ]
    assert "".join(chunks) == "fallback answer"
