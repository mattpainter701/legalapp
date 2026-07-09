from types import SimpleNamespace

import pytest

from app.services.memory_service import MemoryService


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FakeDB:
    async def execute(self, statement):
        return FakeResult(
            [
                SimpleNamespace(
                    role="user",
                    content="Email jane@example.com; SSN 123-45-6789",
                )
            ]
        )


class RecordingLLM:
    def __init__(self):
        self.kwargs = None

    async def complete(self, **kwargs):
        self.kwargs = kwargs
        return "Safe summary", 5, 2


@pytest.mark.asyncio
async def test_memory_summary_scrubs_transcript_before_provider(monkeypatch):
    llm = RecordingLLM()
    service = MemoryService(llm)

    async def fake_route(*args, **kwargs):
        return SimpleNamespace(provider="litellm", model="test-model")

    async def fake_store(**kwargs):
        return None

    monkeypatch.setattr("app.services.memory_service.resolve_llm_route", fake_route)
    monkeypatch.setattr(service, "create_or_update_memory", fake_store)

    await service.summarize_conversation(
        db=FakeDB(),
        user_id="user-id",
        tenant_id="tenant-id",
        conversation_id="conversation-id",
        tenant_name="Firm jane@example.com",
        privacy_mode=True,
    )

    provider_payload = str(llm.kwargs)
    assert "jane@example.com" not in provider_payload
    assert "123-45-6789" not in provider_payload
