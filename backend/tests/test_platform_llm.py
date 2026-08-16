import uuid

import pytest
from httpx import AsyncClient

from app.routers import platform_llm as platform_llm_router
from app.models.llm_provider_key import LLMProviderKey
from app.models.platform import PlatformSetting
from tests.platform_auth_helpers import platform_headers

TEST_PLATFORM_KEY = "test-platform-key-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


class _FakeLiteLLMResponse:
    def __init__(self, status_code: int, payload=None, text: str | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


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


def test_opencode_go_gpt_uses_responses_api_for_provider_canary():
    assert (
        platform_llm_router._provider_api_mode("opencode-go", "gpt-5.6-luna")
        == "responses"
    )
    # It cannot be activated through the app's Chat Completions-only route,
    # but it must remain directly testable from the Platform router.
    assert platform_llm_router._route_compatible("opencode-go", "gpt-5.6-luna") is False


def test_opencode_go_anthropic_compatible_models_use_messages_api_for_canary():
    assert (
        platform_llm_router._provider_api_mode("opencode-go", "qwen3.8-max")
        == "messages"
    )
    assert (
        platform_llm_router._provider_api_mode("opencode-go", "minimax-m3")
        == "messages"
    )


def test_responses_output_text_extracts_openai_response_content():
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "OK"},
                ],
            }
        ]
    }
    assert platform_llm_router._responses_output_text(payload) == "OK"


@pytest.mark.asyncio
async def test_opencode_go_gpt_canary_uses_responses_endpoint(
    client: AsyncClient, db_session, monkeypatch
):
    key = LLMProviderKey(
        id=uuid.uuid4(),
        name="OpenCode Go test key",
        provider_id="opencode-go",
        encrypted_key="encrypted",
        key_hint="hint",
    )
    db_session.add(key)
    await db_session.commit()
    monkeypatch.setattr(platform_llm_router, "decrypt_token", lambda _value: "sk-test")
    fake = _FakeLiteLLMClient(
        [
            _FakeLiteLLMResponse(
                200,
                {
                    "model": "gpt-5.6-luna",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "OK"}],
                        }
                    ],
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 1,
                        "total_tokens": 6,
                    },
                },
            )
        ]
    )
    monkeypatch.setattr(platform_llm_router.httpx, "AsyncClient", lambda **_kwargs: fake)

    response = await client.post(
        "/api/platform/llm/routes/test",
        headers=platform_headers(),
        json={
            "provider_id": "opencode-go",
            "key_id": str(key.id),
            "model": "gpt-5.6-luna",
            "route": "premium",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert fake.calls == [
        (
            "POST",
            "https://opencode.ai/zen/go/v1/responses",
            {
                "model": "gpt-5.6-luna",
                "input": [
                    {
                        "role": "developer",
                        "content": "Follow the user's output format exactly.",
                    },
                    {"role": "user", "content": "Reply with exactly: OK"},
                ],
                "max_output_tokens": platform_llm_router.PROVIDER_CANARY_MAX_TOKENS,
            },
        )
    ]


@pytest.mark.asyncio
async def test_route_activation_canary_has_reasoning_model_token_budget(monkeypatch):
    fake = _FakeLiteLLMClient(
        [
            _FakeLiteLLMResponse(
                200,
                {
                    "model": "deepseek-v4-flash",
                    "choices": [{"message": {"content": "OK"}}],
                },
            ),
            _FakeLiteLLMResponse(
                200,
                {
                    "model": "deepseek-v4-pro",
                    "choices": [{"message": {"content": "OK"}}],
                },
            ),
        ]
    )
    monkeypatch.setattr(
        platform_llm_router.settings, "LITELLM_BASE_URL", "http://litellm"
    )
    monkeypatch.setattr(platform_llm_router.settings, "LITELLM_API_KEY", "test-key")
    monkeypatch.setattr(
        platform_llm_router.httpx, "AsyncClient", lambda **_kwargs: fake
    )

    valid, results, error = await platform_llm_router._probe_litellm_aliases(
        {"standard": "clarity-standard-r1", "premium": "clarity-premium-r1"}
    )

    assert valid is True
    assert error is None
    assert results["premium"]["ok"] is True
    for _, _, payload in fake.calls:
        assert (
            payload["max_tokens"]
            == platform_llm_router.ROUTE_ACTIVATION_CANARY_MAX_TOKENS
        )
        assert payload["messages"][0]["role"] == "system"


def test_both_canaries_leave_room_for_a_reasoning_pass():
    """Reasoning models bill chain-of-thought against max_tokens.

    The activation probe was already widened; the direct provider test was not,
    so operators saw a working reasoning model report itself as broken and
    route recommendations demoted it for lacking a passing canary.
    """

    assert platform_llm_router.ROUTE_ACTIVATION_CANARY_MAX_TOKENS >= 256
    assert platform_llm_router.PROVIDER_CANARY_MAX_TOKENS >= 256


@pytest.mark.parametrize(
    "reply",
    ["OK", "ok", "OK.", " OK \n", '"OK"', "**OK**"],
)
def test_canary_accepts_provider_formatting_variants(reply):
    assert platform_llm_router.canary_answer_matches(reply) is True


@pytest.mark.parametrize("reply", ["", None, "OKAY", "NOT OK", "I cannot comply"])
def test_canary_rejects_non_acknowledgements(reply):
    assert platform_llm_router.canary_answer_matches(reply) is False


def test_canary_reports_reasoning_drain_separately_from_a_wrong_answer():
    """An exhausted reasoning budget is a budget problem, not a dead route."""

    drained_payload = {
        "choices": [{"finish_reason": "length", "message": {"content": ""}}]
    }
    assert platform_llm_router._canary_reasoning_drain(drained_payload, "") is True
    assert (
        platform_llm_router._canary_error_category(False, True)
        == "reasoning_budget_exhausted"
    )
    assert (
        platform_llm_router._canary_error_category(False, False)
        == "unexpected_response"
    )
    assert platform_llm_router._canary_error_category(True, False) is None

    reasoning_only = {
        "choices": [{"message": {"content": "", "reasoning_content": "thinking..."}}]
    }
    assert platform_llm_router._canary_reasoning_drain(reasoning_only, "") is True
    # Visible content always wins: a real answer is never a drain.
    assert platform_llm_router._canary_reasoning_drain(drained_payload, "OK") is False


@pytest.mark.asyncio
async def test_route_activation_names_a_drained_reasoning_budget(monkeypatch):
    fake = _FakeLiteLLMClient(
        [
            _FakeLiteLLMResponse(
                200,
                {
                    "model": "deepseek-v4-pro",
                    "choices": [
                        {"finish_reason": "length", "message": {"content": ""}}
                    ],
                },
            ),
        ]
    )
    monkeypatch.setattr(
        platform_llm_router.settings, "LITELLM_BASE_URL", "http://litellm"
    )
    monkeypatch.setattr(platform_llm_router.settings, "LITELLM_API_KEY", "test-key")
    monkeypatch.setattr(
        platform_llm_router.httpx, "AsyncClient", lambda **_kwargs: fake
    )

    valid, results, error = await platform_llm_router._probe_litellm_aliases(
        {"standard": "clarity-standard-r1"}
    )

    assert valid is False
    assert results["standard"]["reasoning_drain"] is True
    assert "reasoning" in error
    # The operator must be told the provider was reached, not that it is down.
    assert "reached the provider" in error


def test_model_catalog_capabilities_from_provider_metadata():
    model = platform_llm_router._normalize_model_item(
        {
            "id": "google/gemma-4-26b-a4b-it:free",
            "description": "Instruction-tuned multimodal model with structured output.",
            "context_length": 262144,
            "architecture": {
                "modality": "text+image+file->text",
                "input_modalities": ["text", "image", "file"],
                "output_modalities": ["text"],
            },
            "supported_parameters": ["tools", "response_format"],
            "pricing": {"prompt": "0", "completion": "0"},
        },
        "openrouter",
    )

    assert model["is_free"] is True
    assert set(model["capabilities"]).issuperset(
        {
            "vision",
            "text_input",
            "file_input",
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


def test_model_catalog_derives_audio_transcription_and_embedding_modalities():
    transcription = platform_llm_router._normalize_model_item(
        {
            "id": "provider/transcribe",
            "architecture": {
                "input_modalities": ["audio"],
                "output_modalities": ["text"],
            },
            "pricing": {"prompt": "0.001", "completion": "0"},
        },
        "openrouter",
    )
    embedding = platform_llm_router._normalize_model_item(
        {
            "id": "provider/embedding",
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["embeddings"],
            },
            "pricing": {"prompt": "0.001", "completion": "0"},
        },
        "openrouter",
    )

    assert {"audio_input", "speech_to_text"}.issubset(transcription["capabilities"])
    assert {"text_input", "embeddings"}.issubset(embedding["capabilities"])


@pytest.mark.parametrize(
    ("status", "category", "credential_state"),
    (
        (401, "invalid_credentials", "invalid"),
        (403, "billing_or_provider_policy", "indeterminate_policy_block"),
        (429, "rate_limited", "accepted_but_blocked"),
        (500, "provider_unavailable", "indeterminate"),
    ),
)
def test_provider_errors_are_redacted_and_classified(
    status, category, credential_state
):
    request = platform_llm_router.httpx.Request("POST", "https://provider.invalid")
    response = platform_llm_router.httpx.Response(
        status,
        request=request,
        text='{"secret_workspace_id":"must-not-escape"}',
    )
    error = platform_llm_router.httpx.HTTPStatusError(
        "provider response contains must-not-escape",
        request=request,
        response=response,
    )

    evidence = platform_llm_router._provider_error_evidence(error)

    assert evidence["http_status"] == status
    assert evidence["error_category"] == category
    assert evidence["credential_state"] == credential_state
    assert "must-not-escape" not in str(evidence)


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


def test_paid_go_model_is_eligible_and_not_excluded_for_cost():
    model = platform_llm_router._normalize_model_item(
        {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
        "opencode-go",
    )

    assert model["economic_tier"] == "paid"
    assert model["api_mode"] == "chat_completions"
    assert model["route_compatible"] is True
    assert model["legal_eligible"] is True
    assert "not_free" not in model["exclusion_reasons"]


def test_all_go_models_inherit_documented_zero_retention_policy():
    model = platform_llm_router._normalize_model_item(
        {"id": "mimo-v2.5", "name": "MiMo V2.5"},
        "opencode-go",
    )

    assert model["data_policy"] == "zero_retention"
    assert model["confidential_data_allowed"] is True


def test_zen_free_model_is_cataloged_as_demo_only():
    model = platform_llm_router._normalize_model_item(
        {"id": "big-pickle", "name": "Big Pickle"},
        "opencode-zen",
    )

    assert model["is_free"] is True
    assert model["confidential_data_allowed"] is False
    assert model["data_policy"] == "training_or_improvement_possible"


def test_catalog_merge_preserves_all_provider_keys_without_duplicate_models():
    models = platform_llm_router._merge_catalog_models(
        [
            {
                "id": "deepseek-v4-pro",
                "provider_id": "opencode-go",
                "key_id": "key-a",
                "key_name": "Go A",
            },
            {
                "id": "deepseek-v4-pro",
                "provider_id": "opencode-go",
                "key_id": "key-b",
                "key_name": "Go B",
            },
        ]
    )

    assert len(models) == 1
    assert models[0]["key_ids"] == ["key-a", "key-b"]
    assert models[0]["key_count"] == 2
    assert models[0]["key_name"] == "2 stored keys"


def test_route_recommendation_uses_canary_health_and_customer_data_policy():
    now = platform_llm_router.datetime.now(platform_llm_router.timezone.utc)
    catalog = {
        "models": [
            {
                "id": "deepseek-v4-pro",
                "name": "DeepSeek V4 Pro",
                "provider_id": "opencode-go",
                "provider_name": "OpenCode Go",
                "key_ids": ["key-go"],
                "legal_eligible": True,
                "legal_tier": "usable",
                "legal_score": 5,
                "route_compatible": True,
                "capabilities": ["text_input", "instruction"],
                "confidential_data_allowed": True,
                "data_policy": "zero_retention",
            },
            {
                "id": "deepseek-v4-pro",
                "name": "DeepSeek V4 Pro",
                "provider_id": "deepseek",
                "provider_name": "DeepSeek",
                "key_ids": ["key-direct"],
                "legal_eligible": True,
                "legal_tier": "usable",
                "legal_score": 5,
                "route_compatible": True,
                "capabilities": ["text_input", "instruction"],
                "confidential_data_allowed": None,
                "data_policy": "provider_terms",
            },
            {
                "id": "laguna-s-2.1-free",
                "name": "Laguna",
                "provider_id": "opencode-zen",
                "provider_name": "OpenCode Zen",
                "key_ids": ["key-zen"],
                "legal_eligible": True,
                "legal_tier": "usable",
                "legal_score": 5,
                "route_compatible": True,
                "capabilities": ["text_input", "instruction"],
                "is_free": True,
                "confidential_data_allowed": False,
                "data_policy": "training_or_improvement_possible",
            },
        ]
    }
    keys = [
        {"id": "key-go", "name": "Go", "provider_id": "opencode-go"},
        {"id": "key-direct", "name": "Direct", "provider_id": "deepseek"},
        {"id": "key-zen", "name": "Zen", "provider_id": "opencode-zen"},
    ]
    health = {
        ("opencode-go", "deepseek-v4-pro", "key-go"): {
            "ok": False,
            "error_category": "billing_or_provider_policy",
            "tested_at": now,
        },
        ("deepseek", "deepseek-v4-pro", "key-direct"): {
            "ok": True,
            "tested_at": now,
        },
    }

    result = platform_llm_router._recommend_route_targets(
        catalog=catalog,
        keys=keys,
        health=health,
        criteria=platform_llm_router.RouteRecommendationRequest(
            route="premium", cost_preference="quality", data_mode="customer"
        ),
        now=now,
    )

    assert result["candidates"] == []
    assert any("requested 3" in warning for warning in result["warnings"])


def test_route_recommendation_can_select_free_demo_capacity():
    catalog = {
        "models": [
            {
                "id": "laguna-s-2.1-free",
                "provider_id": "opencode-zen",
                "provider_name": "OpenCode Zen",
                "key_ids": ["key-zen"],
                "legal_eligible": True,
                "legal_tier": "usable",
                "legal_score": 5,
                "route_compatible": True,
                "capabilities": ["text_input", "instruction"],
                "is_free": True,
                "confidential_data_allowed": False,
                "data_policy": "training_or_improvement_possible",
            }
        ]
    }

    result = platform_llm_router._recommend_route_targets(
        catalog=catalog,
        keys=[{"id": "key-zen", "name": "Zen", "provider_id": "opencode-zen"}],
        health={},
        criteria=platform_llm_router.RouteRecommendationRequest(
            route="standard",
            cost_preference="free_only",
            data_mode="demo",
            count=1,
        ),
    )

    assert result["candidates"][0]["model"] == "laguna-s-2.1-free"
    assert result["candidates"][0]["is_free"] is True


@pytest.mark.asyncio
async def test_route_recommendation_endpoint_returns_auditable_targets(
    client: AsyncClient, db_session
):
    key = LLMProviderKey(
        id=uuid.uuid4(),
        name="OpenCode Go",
        provider_id="opencode-go",
        encrypted_key="unused",
        key_hint="hint",
    )
    db_session.add(key)
    db_session.add(
        PlatformSetting(
            key=platform_llm_router.LLM_MODEL_CATALOG_KEY,
            value={
                "models": [
                    {
                        "id": "deepseek-v4-pro",
                        "name": "DeepSeek V4 Pro",
                        "provider_id": "opencode-go",
                        "provider_name": "OpenCode Go",
                        "key_id": str(key.id),
                        "key_ids": [str(key.id)],
                        "legal_eligible": True,
                        "legal_tier": "usable",
                        "legal_score": 5,
                        "route_compatible": True,
                        "capabilities": ["text_input", "instruction"],
                        "confidential_data_allowed": True,
                        "data_policy": "zero_retention",
                    }
                ]
            },
        )
    )
    await db_session.commit()

    response = await client.post(
        "/api/platform/llm/routes/recommend",
        headers=platform_headers(),
        json={
            "route": "premium",
            "cost_preference": "quality",
            "data_mode": "customer",
            "count": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["candidates"][0]["model"] == "deepseek-v4-pro"
    assert payload["candidates"][0]["key_id"] == str(key.id)


def test_customer_route_policy_fails_closed_without_explicit_approval():
    config = {
        "standard": {
            "provider_id": "openrouter",
            "model": "provider/paid-standard",
            "alternates": [
                {
                    "provider_id": "opencode-zen",
                    "model": "nemotron-3-ultra-free",
                }
            ],
            "fallbacks": [
                {
                    "provider_id": "openrouter",
                    "model": "google/gemma-4-31b-it:free",
                }
            ],
        },
        "premium": {
            "provider_id": "anthropic",
            "model": "claude-3-5-sonnet-latest",
        },
    }

    blocked = platform_llm_router._confidential_data_unsafe_targets(
        config, {"models": []}
    )

    assert [(item["route"], item["placement"]) for item in blocked] == [
        ("standard", "primary"),
        ("standard", "alternate[0]"),
        ("standard", "fallback[0]"),
        ("premium", "primary"),
    ]
    assert all(item["reason"] == "not_approved" for item in blocked)


def test_customer_route_policy_uses_catalog_data_policy_metadata():
    config = {
        "standard": {
            "provider_id": "provider-a",
            "model": "model-without-free-suffix",
        },
        "premium": {},
    }
    catalog = {
        "models": [
            {
                "provider_id": "provider-a",
                "id": "model-without-free-suffix",
                "pricing": {"prompt": "0", "completion": "0.000000"},
                "confidential_data_allowed": False,
            }
        ]
    }

    blocked = platform_llm_router._confidential_data_unsafe_targets(config, catalog)

    assert blocked == [
        {
            "route": "standard",
            "placement": "primary",
            "provider_id": "provider-a",
            "model": "model-without-free-suffix",
            "data_policy": "unknown",
            "reason": "disallowed",
        }
    ]


@pytest.mark.asyncio
async def test_customer_route_policy_rejection_commits_audit(monkeypatch):
    audit_call = {}

    class _FakeDb:
        committed = False

        async def commit(self):
            self.committed = True

    async def fake_catalog(_db):
        return {"models": []}

    async def fake_audit(db, request, **kwargs):
        audit_call.update(db=db, request=request, **kwargs)

    monkeypatch.setattr(platform_llm_router, "_get_model_catalog", fake_catalog)
    monkeypatch.setattr(platform_llm_router, "record_operator_audit", fake_audit)
    db = _FakeDb()
    request = object()

    with pytest.raises(platform_llm_router.HTTPException) as raised:
        await platform_llm_router._enforce_customer_route_data_policy(
            request,
            db,
            {
                "standard": {
                    "provider_id": "opencode-zen",
                    "model": "nemotron-3-ultra-free",
                },
                "premium": {},
            },
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "confidential_data_not_allowed"
    assert db.committed is True
    assert audit_call["action"] == "llm.routes_activation_blocked"
    assert audit_call["metadata"]["reason"] == "confidential_data_not_allowed"


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
        id=uuid.uuid4(),
        name="Standard key",
        provider_id="opencode-go",
        encrypted_key="unused",
        key_hint="test",
    )
    premium_key = LLMProviderKey(
        id=uuid.uuid4(),
        name="Premium key",
        provider_id="opencode-go",
        encrypted_key="unused",
        key_hint="test",
    )
    db_session.add_all([standard_key, premium_key])
    db_session.add(
        PlatformSetting(
            key=platform_llm_router.LLM_MODEL_CATALOG_KEY,
            value={
                "models": [
                    {
                        "id": "deepseek-v4-flash",
                        "provider_id": "opencode-go",
                        "key_ids": [str(standard_key.id)],
                        "legal_eligible": True,
                        "route_compatible": True,
                    },
                    {
                        "id": "deepseek-v4-pro",
                        "provider_id": "opencode-go",
                        "key_ids": [str(premium_key.id)],
                        "legal_eligible": True,
                        "route_compatible": True,
                    },
                ]
            },
        )
    )
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
                "provider_id": "opencode-go",
                "key_id": str(standard_key.id),
                "model": "deepseek-v4-flash",
            },
            "premium": {
                "provider_id": "opencode-go",
                "key_id": str(premium_key.id),
                "model": "deepseek-v4-pro",
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
