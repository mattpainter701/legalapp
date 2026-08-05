import uuid

import pytest
from httpx import AsyncClient

from app.routers import platform_llm as platform_llm_router
from app.models.llm_provider_key import LLMProviderKey
from tests.platform_auth_helpers import platform_headers

TEST_PLATFORM_KEY = "test-platform-key-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


class _FakeLiteLLMResponse:
    def __init__(self, status_code: int, payload=None, text: str | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else ""

    def json(self):
        return self._payload


class _FakeLiteLLMClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def _next_response(self, method, url, json_payload=None):
        self.calls.append((method, url, json_payload))
        return self.responses.pop(0)

    async def get(self, url, headers=None):
        return self._next_response("GET", url)

    async def post(self, url, headers=None, json=None):
        return self._next_response("POST", url, json)

    async def patch(self, url, headers=None, json=None):
        return self._next_response("PATCH", url, json)


@pytest.mark.asyncio
async def test_platform_llm_config_round_trip(client: AsyncClient):
    headers = platform_headers()

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
    assert (
        openrouter["litellm_params"]["model"] == "openrouter/qwen/qwen3-235b-a22b:free"
    )
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
        {
            "vision",
            "instruction",
            "tool_use",
            "structured_output",
            "large_context",
            "rag",
        }
    )
    assert model["legal_eligible"] is True
    assert model["legal_tier"] == "recommended"
    assert "Legal-ready" in model["eligibility_badges"]


def test_model_catalog_marks_document_capable_free_legal_model():
    model = platform_llm_router._normalize_model_item(
        {
            "id": "meta-llama/llama-4-maverick-instruct:free",
            "description": "Instruction model for reasoning, RAG, document understanding, PDF analysis, and structured output.",
            "context_length": 1048576,
            "architecture": {"modality": "text+image->text"},
            "supported_parameters": ["response_format"],
            "pricing": {"prompt": "0", "completion": "0"},
            "latency_ms": 2400,
        },
        "openrouter",
    )

    assert model["legal_eligible"] is True
    assert model["legal_tier"] == "recommended"
    assert model["latency_eligible"] is True
    assert "Document-capable" in model["eligibility_badges"]
    assert model["exclusion_reasons"] == []


def test_model_catalog_excludes_coding_only_free_model():
    model = platform_llm_router._normalize_model_item(
        {
            "id": "qwen/qwen-coder-7b-instruct:free",
            "description": "Coding assistant specialized for programming and software engineering.",
            "context_length": 32768,
            "supported_parameters": ["tools"],
            "pricing": {"prompt": "0", "completion": "0"},
        },
        "openrouter",
    )

    assert model["legal_eligible"] is False
    assert model["legal_tier"] == "excluded"
    assert "coding_specialized" in model["exclusion_reasons"]


def test_model_catalog_excludes_slow_free_model():
    model = platform_llm_router._normalize_model_item(
        {
            "id": "qwen/qwen3-235b-a22b-instruct:free",
            "description": "Instruction reasoning model with RAG and structured output.",
            "context_length": 262144,
            "supported_parameters": ["response_format", "reasoning"],
            "pricing": {"prompt": "0", "completion": "0"},
            "latency_ms": 4200,
        },
        "openrouter",
    )

    assert model["legal_eligible"] is False
    assert model["legal_tier"] == "excluded"
    assert model["latency_eligible"] is False
    assert "slow_latency" in model["exclusion_reasons"]


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
        {"standard": "clarity-standard-rtest", "premium": "clarity-premium-rtest"},
    )

    assert [model["model_name"] for model in models] == [
        "clarity-standard-rtest",
        "clarity-standard-rtest",
    ]
    assert models[0]["model_info"]["id"] != models[1]["model_info"]["id"]
    assert (
        models[0]["litellm_params"]["model"] == "openrouter/qwen/qwen3-235b-a22b:free"
    )
    assert models[0]["litellm_params"]["weight"] == 80
    assert models[1]["litellm_params"]["model"] == "openai/deepseek-v4-flash-free"
    assert fallbacks == []
    assert errors == [
        "standard fallback 1: selected key belongs to openrouter, not deepseek"
    ]


def test_litellm_reload_payload_builds_fast_standard_route(monkeypatch):
    openrouter_key = LLMProviderKey(
        id=uuid.uuid4(),
        name="OpenRouter",
        provider_id="openrouter",
        encrypted_key="encrypted-openrouter",
        key_hint="test",
    )
    opencode_key = LLMProviderKey(
        id=uuid.uuid4(),
        name="OpenCode Zen",
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
                "model": "google/gemma-4-31b-it:free",
                "capacity": 100,
                "fallbacks": [
                    {
                        "key_id": str(opencode_key.id),
                        "provider_id": "opencode-zen",
                        "model": "nemotron-3-ultra-free",
                        "capacity": 100,
                    },
                    {
                        "key_id": str(opencode_key.id),
                        "provider_id": "opencode-zen",
                        "model": "deepseek-v4-flash-free",
                        "capacity": 100,
                    },
                ],
            },
            "premium": {},
        },
        {str(openrouter_key.id): openrouter_key, str(opencode_key.id): opencode_key},
        {"standard": "clarity-standard-rtest", "premium": "clarity-premium-rtest"},
    )

    assert [model["model_name"] for model in models] == [
        "clarity-standard-rtest",
        "clarity-standard-rtest-fb-0",
        "clarity-standard-rtest-fb-1",
    ]
    assert (
        models[0]["litellm_params"]["model"] == "openrouter/google/gemma-4-31b-it:free"
    )
    assert models[1]["litellm_params"]["model"] == "openai/nemotron-3-ultra-free"
    assert models[1]["litellm_params"]["api_base"] == "https://opencode.ai/zen/v1"
    assert models[2]["litellm_params"]["model"] == "openai/deepseek-v4-flash-free"
    assert fallbacks == [
        {
            "clarity-standard-rtest": [
                "clarity-standard-rtest-fb-0",
                "clarity-standard-rtest-fb-1",
            ]
        }
    ]
    assert errors == []


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
async def test_litellm_reload_uses_model_management_api(monkeypatch):
    fake_client = _FakeLiteLLMClient(
        [
            _FakeLiteLLMResponse(
                200,
                {
                    "data": [
                        {
                            "model_name": "clarity-standard",
                            "litellm_params": {
                                "model": "openrouter/google/gemma-4-31b-it:free",
                            },
                            "model_info": {"id": "static-standard", "db_model": False},
                        },
                        {
                            "model_name": "clarity-standard-fb-0",
                            "litellm_params": {
                                "model": "openai/nemotron-3-ultra-free",
                            },
                            "model_info": {"id": "db-fallback-0", "db_model": True},
                        },
                    ]
                },
            ),
            _FakeLiteLLMResponse(200, {"ok": True}),
            _FakeLiteLLMResponse(200, {"ok": True}),
            _FakeLiteLLMResponse(200, {"message": "Config updated successfully"}),
        ]
    )
    monkeypatch.setattr(
        platform_llm_router.settings, "LITELLM_BASE_URL", "http://litellm"
    )
    monkeypatch.setattr(platform_llm_router.settings, "LITELLM_API_KEY", "sk-master")
    monkeypatch.setattr(
        platform_llm_router.httpx,
        "AsyncClient",
        lambda timeout: fake_client,
    )

    ok, error = await platform_llm_router._call_litellm_config_update(
        [
            {
                "model_name": "clarity-standard",
                "litellm_params": {
                    "model": "openrouter/google/gemma-4-31b-it:free",
                    "api_key": "sk-openrouter",
                },
            },
            {
                "model_name": "clarity-standard-fb-0",
                "litellm_params": {
                    "model": "openai/nemotron-3-ultra-free",
                    "api_key": "sk-opencode",
                    "api_base": "https://opencode.ai/zen/v1",
                },
            },
            {
                "model_name": "clarity-standard-fb-1",
                "litellm_params": {
                    "model": "openai/deepseek-v4-flash-free",
                    "api_key": "sk-opencode",
                    "api_base": "https://opencode.ai/zen/v1",
                },
            },
        ],
        [{"clarity-standard": ["clarity-standard-fb-0", "clarity-standard-fb-1"]}],
    )

    assert ok is True
    assert error is None
    assert [call[:2] for call in fake_client.calls] == [
        ("GET", "http://litellm/model/info"),
        ("PATCH", "http://litellm/model/db-fallback-0/update"),
        ("POST", "http://litellm/model/new"),
        ("POST", "http://litellm/config/update"),
    ]
    config_update = fake_client.calls[-1][2]
    assert "model_list" not in config_update
    assert config_update["router_settings"]["fallbacks"] == [
        {"clarity-standard": ["clarity-standard-fb-0", "clarity-standard-fb-1"]}
    ]


@pytest.mark.asyncio
async def test_litellm_upserts_each_balanced_deployment_by_stable_id(monkeypatch):
    fake_client = _FakeLiteLLMClient(
        [
            _FakeLiteLLMResponse(200, {"data": []}),
            _FakeLiteLLMResponse(201, {"ok": True}),
            _FakeLiteLLMResponse(201, {"ok": True}),
            _FakeLiteLLMResponse(200, {"message": "updated"}),
        ]
    )
    monkeypatch.setattr(
        platform_llm_router.settings, "LITELLM_BASE_URL", "http://litellm"
    )
    monkeypatch.setattr(platform_llm_router.settings, "LITELLM_API_KEY", "sk-master")
    monkeypatch.setattr(
        platform_llm_router.httpx,
        "AsyncClient",
        lambda timeout: fake_client,
    )

    ok, error = await platform_llm_router._call_litellm_config_update(
        [
            {
                "model_name": "clarity-standard-rtest",
                "litellm_params": {"model": "openrouter/model-a", "api_key": "a"},
                "model_info": {"id": "deployment-a", "legalapp_managed": True},
            },
            {
                "model_name": "clarity-standard-rtest",
                "litellm_params": {"model": "openrouter/model-b", "api_key": "b"},
                "model_info": {"id": "deployment-b", "legalapp_managed": True},
            },
        ],
        [],
    )

    assert ok is True
    assert error is None
    new_model_calls = [
        call for call in fake_client.calls if call[1].endswith("/model/new")
    ]
    assert [call[2]["model_info"]["id"] for call in new_model_calls] == [
        "deployment-a",
        "deployment-b",
    ]


@pytest.mark.asyncio
async def test_litellm_reload_rejects_different_file_backed_alias(monkeypatch):
    fake_client = _FakeLiteLLMClient(
        [
            _FakeLiteLLMResponse(
                200,
                {
                    "data": [
                        {
                            "model_name": "clarity-standard",
                            "litellm_params": {
                                "model": "openrouter/meta-llama/llama-3.3-70b-instruct:free",
                            },
                            "model_info": {"id": "static-standard", "db_model": False},
                        }
                    ]
                },
            )
        ]
    )
    monkeypatch.setattr(
        platform_llm_router.settings, "LITELLM_BASE_URL", "http://litellm"
    )
    monkeypatch.setattr(platform_llm_router.settings, "LITELLM_API_KEY", "sk-master")
    monkeypatch.setattr(
        platform_llm_router.httpx,
        "AsyncClient",
        lambda timeout: fake_client,
    )

    ok, error = await platform_llm_router._call_litellm_config_update(
        [
            {
                "model_name": "clarity-standard",
                "litellm_params": {
                    "model": "openrouter/google/gemma-4-31b-it:free",
                    "api_key": "sk-openrouter",
                },
            }
        ],
        [],
    )

    assert ok is False
    assert "file-backed" in error
    assert [call[:2] for call in fake_client.calls] == [
        ("GET", "http://litellm/model/info")
    ]


@pytest.mark.asyncio
async def test_provider_route_builder_rejects_mismatched_key_provider(
    client: AsyncClient, db_session
):
    headers = platform_headers()

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
async def test_failed_route_activation_does_not_publish_candidate_config(
    client: AsyncClient, db_session, monkeypatch
):
    headers = platform_headers()
    standard_key = LLMProviderKey(
        name="Standard key",
        provider_id="openrouter",
        encrypted_key="unused",
        key_hint="test",
    )
    premium_key = LLMProviderKey(
        name="Premium key",
        provider_id="anthropic",
        encrypted_key="unused",
        key_hint="test",
    )
    db_session.add_all([standard_key, premium_key])
    await db_session.commit()
    await db_session.refresh(standard_key)
    await db_session.refresh(premium_key)

    async def failed_reload(*args, **kwargs):
        return {
            "litellm_updated": False,
            "litellm_error": "synthetic premium completion failed",
            "models_registered": 2,
            "fallbacks_registered": 0,
            "build_errors": [],
            "app_aliases": kwargs["aliases"],
            "validation": {"standard": {"ok": True}},
        }

    monkeypatch.setattr(platform_llm_router, "_reload_litellm_routes", failed_reload)

    async def gateway_status(*args, **kwargs):
        return {"reachable": True, "aliases": {}}

    monkeypatch.setattr(platform_llm_router, "_check_litellm_gateway", gateway_status)
    response = await client.put(
        "/api/platform/llm/routes",
        headers=headers,
        json={
            "standard": {
                "provider_id": "openrouter",
                "key_id": str(standard_key.id),
                "model": "provider/standard",
            },
            "premium": {
                "provider_id": "anthropic",
                "key_id": str(premium_key.id),
                "model": "claude-premium",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["saved"] is False
    routes = (await client.get("/api/platform/llm/routes", headers=headers)).json()
    assert routes["standard"].get("key_id") is None
    assert routes["premium"].get("key_id") is None


@pytest.mark.asyncio
async def test_provider_key_invalid_uuid_returns_400(client: AsyncClient):
    headers = platform_headers()

    resp = await client.delete(
        "/api/platform/llm/provider-keys/not-a-uuid",
        headers=headers,
    )
    assert resp.status_code == 400
