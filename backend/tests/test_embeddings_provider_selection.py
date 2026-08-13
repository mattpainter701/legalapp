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
