from types import SimpleNamespace

import pytest

from app.services import embeddings as embeddings_module


class _ClientCapture:
    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs):
        self.calls.append(kwargs)


def _configure(monkeypatch, **overrides):
    defaults = {
        "LITELLM_ENABLED": False,
        "LITELLM_EMBEDDING_MODEL": "",
        "OPENAI_API_KEY": "",
        "OPENROUTER_API_KEY": "",
        "OPENROUTER_BASE_URL": "https://openrouter.example/v1",
        "OPENROUTER_EMBEDDING_MODEL": "openai/text-embedding-3-small",
        "OPENCODE_KEY": "",
        "DEEPSEEK_API_KEY": "",
        "DEEPSEEK_BASE_URL": "https://chat-only.example/v1",
        "EMBEDDING_MODEL": "text-embedding-3-small",
        "EMBEDDING_DIM": 1536,
    }
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(embeddings_module.settings, name, value)
    _ClientCapture.calls.clear()
    monkeypatch.setattr(embeddings_module, "AsyncOpenAI", _ClientCapture)


def test_openrouter_is_embedding_fallback(monkeypatch):
    _configure(monkeypatch, OPENROUTER_API_KEY="sk-valid-token")

    service = embeddings_module.EmbeddingService()

    assert service.model == "openai/text-embedding-3-small"
    assert _ClientCapture.calls == [
        {
            "api_key": "sk-valid-token",
            "base_url": "https://openrouter.example/v1",
        }
    ]


def test_openai_remains_preferred_embedding_provider(monkeypatch):
    _configure(
        monkeypatch,
        OPENAI_API_KEY="sk-openai-token",
        OPENROUTER_API_KEY="sk-openrouter-token",
    )

    service = embeddings_module.EmbeddingService()

    assert service.model == "text-embedding-3-small"
    assert _ClientCapture.calls == [{"api_key": "sk-openai-token"}]


def test_chat_only_or_template_keys_do_not_create_embedding_client(monkeypatch):
    _configure(
        monkeypatch,
        OPENROUTER_API_KEY="configured only on production host",
        OPENCODE_KEY="sk-opencode-token",
        DEEPSEEK_API_KEY="sk-deepseek-token",
    )

    service = embeddings_module.EmbeddingService()

    assert service.client is None
    assert service.model is None
    assert _ClientCapture.calls == []


@pytest.mark.asyncio
async def test_vaulted_openrouter_key_uses_zdr_and_expected_dimensions(monkeypatch):
    _configure(monkeypatch)

    key = SimpleNamespace(
        encrypted_key="encrypted-openrouter-token",
        updated_at=None,
        created_at=None,
    )

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def scalar(self, _statement):
            return key

    calls = []

    class _Embeddings:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=[0.25] * 1536)]
            )

    class _EmbeddingClient:
        def __init__(self, **kwargs):
            self.init = kwargs
            self.embeddings = _Embeddings()

    monkeypatch.setattr(embeddings_module, "async_session_maker", _Session)
    monkeypatch.setattr(
        embeddings_module,
        "decrypt_token",
        lambda encrypted: "sk-vault-openrouter" if encrypted else "",
    )
    monkeypatch.setattr(embeddings_module, "AsyncOpenAI", _EmbeddingClient)

    service = embeddings_module.EmbeddingService()
    result = await service.embed_batch(["privileged tenant document"])

    assert len(result) == 1
    assert len(result[0]) == 1536
    assert service.provider_id == "openrouter"
    assert service.model == "openai/text-embedding-3-small"
    assert calls == [
        {
            "model": "openai/text-embedding-3-small",
            "input": ["privileged tenant document"],
            "dimensions": 1536,
            "extra_body": {"provider": {"zdr": True, "data_collection": "deny"}},
        }
    ]


@pytest.mark.asyncio
async def test_wrong_dimension_embedding_fails_closed(monkeypatch):
    _configure(monkeypatch, OPENROUTER_API_KEY="sk-valid-token")

    class _Embeddings:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=[0.25] * 1024)]
            )

    service = embeddings_module.EmbeddingService()
    service.client = SimpleNamespace(embeddings=_Embeddings())

    assert await service.embed_batch(["tenant document"]) == []
