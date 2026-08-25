"""
Platform LLM Provider Route Builder.

Authenticated by short-lived, scoped platform bearer tokens (same as
``platform.py``).

Endpoints:
  GET  /api/platform/llm/providers                    — list provider presets
  GET  /api/platform/llm/provider-keys                — list stored keys (masked)
  POST /api/platform/llm/provider-keys                — add encrypted key
  DELETE /api/platform/llm/provider-keys/{id}         — delete key
  POST /api/platform/llm/provider-keys/sync-env       — import env vars into vault
  POST /api/platform/llm/provider-keys/{id}/fetch-models — list models from provider
  GET  /api/platform/llm/routes                       — current route config
  POST /api/platform/llm/routes/recommend             — rank a primary + fallbacks
  PUT  /api/platform/llm/routes                       — save routes (hot-reloads LiteLLM)
  GET  /api/platform/llm/gateway/status               — LiteLLM reachability + alias status
  POST /api/platform/llm/routes/reload                — reload saved routes into LiteLLM
  POST /api/platform/llm/routes/test                  — test a route with synthetic prompt
"""

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.llm_provider_key import LLMProviderKey
from app.models.operator_audit import OperatorAuditLog
from app.models.platform import PlatformSetting
from app.models.llm_routing_profile import LLMRoutingProfile
from app.services.llm_routing import (
    LITELLM_PROVIDER,
    get_platform_llm_config,
    upsert_platform_llm_config,
)
from app.services.operator_audit import record_operator_audit
from app.services.platform_auth import require_platform_token
from app.services.token_vault import decrypt_token, encrypt_token

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform/llm", tags=["platform-llm"])

LLM_ROUTE_CONFIG_KEY = "llm_route_config_v2"
LLM_MODEL_CATALOG_KEY = "llm_model_catalog_v1"
LEGAL_READY_LATENCY_MS = 3000
MIN_LEGAL_CONTEXT_LENGTH = 16000
RECOMMENDED_LEGAL_CONTEXT_LENGTH = 100000
CONFIDENTIAL_DATA_BLOCK_CODE = "confidential_data_not_allowed"
# Reasoning models bill chain-of-thought against ``max_tokens`` and emit it
# before any visible content, so a budget sized for the literal answer ("OK")
# makes a healthy reasoning model look dead: 200, empty ``content``, and a
# ``length`` finish reason. The route-activation probe already accounts for this;
# the direct provider test needs the same headroom, because operators read its
# verdict as "is this model usable" and route recommendations key off it.
PROVIDER_CANARY_MAX_TOKENS = 512
ROUTE_ACTIVATION_CANARY_MAX_TOKENS = 512

# Customer prompts may contain privileged or otherwise confidential matter data.
# OpenRouter only qualifies for those routes when every request is forced onto a
# zero-data-retention endpoint and provider data collection is denied. Keep the
# controls in the registered LiteLLM deployment rather than relying on an
# account dashboard toggle, so they travel with every request and fail closed
# when no compliant upstream is available.
#
# OpenRouter ZDR: https://openrouter.ai/docs/guides/features/zdr
OPENROUTER_CONFIDENTIAL_PROVIDER_PREFERENCES = {
    "zdr": True,
    "data_collection": "deny",
}

ZEN_FREE_MODELS = {
    "big-pickle",
    "deepseek-v4-flash-free",
    "hy3-free",
    "laguna-s-2.1-free",
    "mimo-v2.5-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
}

# ── Auth ────────────────────────────────────────────────────────────────────


def _require_platform_key(request: Request) -> None:
    require_platform_token(request)


# ── Provider presets ────────────────────────────────────────────────────────

PROVIDER_PRESETS = [
    {
        "id": "opencode-zen",
        "name": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "models_url": "https://opencode.ai/zen/v1/models",
        "description": "Enterprise & free models (DeepSeek, Nemotron, Kimi, …)",
        "auth_scheme": "bearer",
        "litellm_mode": "openai_compatible",
        "model_placeholder": "deepseek-v4-flash-free",
    },
    {
        "id": "opencode-go",
        "name": "OpenCode Go",
        "base_url": "https://opencode.ai/zen/go/v1",
        "models_url": "https://opencode.ai/zen/go/v1/models",
        "description": "Premium DeepSeek V4 Pro / Flash",
        "auth_scheme": "bearer",
        "litellm_mode": "openai_compatible",
        "model_placeholder": "deepseek-v4-pro",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models_url": "https://openrouter.ai/api/v1/models",
        "description": "200+ models from every major provider",
        "auth_scheme": "bearer",
        "litellm_mode": "openrouter",
        "model_placeholder": "qwen/qwen3-235b-a22b:free",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models_url": "https://api.deepseek.com/v1/models",
        "description": "DeepSeek native API",
        "auth_scheme": "bearer",
        "litellm_mode": "openai_compatible",
        "model_placeholder": "deepseek-chat",
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "models_url": None,
        "description": "Claude models via LiteLLM native integration",
        "auth_scheme": "x-api-key",
        "litellm_mode": "anthropic",
        "model_placeholder": "claude-3-5-sonnet-latest",
        "default_models": [
            {"id": "claude-3-5-sonnet-latest", "name": "Claude 3.5 Sonnet"},
            {"id": "claude-3-5-haiku-latest", "name": "Claude 3.5 Haiku"},
        ],
    },
]

_PRESET_BY_ID = {p["id"]: p for p in PROVIDER_PRESETS}


# ── Schemas ─────────────────────────────────────────────────────────────────


class ProviderKeyCreate(BaseModel):
    name: str
    provider_id: str
    api_key: str

    @field_validator("name", "provider_id", "api_key")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class RouteEntry(BaseModel):
    key_id: Optional[str] = None
    provider_id: Optional[str] = None
    model: Optional[str] = None
    capacity: Optional[int] = 100
    alternates: list[dict[str, Any]] = Field(default_factory=list)
    fallbacks: list[dict[str, Any]] = Field(default_factory=list)
    allow_matter_context: bool = False


class RoutesUpdate(BaseModel):
    standard: RouteEntry
    premium: RouteEntry


class RoutingProfileCreate(BaseModel):
    name: str
    description: Optional[str] = None
    clone_profile_id: Optional[str] = None


class RoutingProfileUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class RouteTestRequest(BaseModel):
    key_id: str
    provider_id: str
    model: str
    route: str = "standard"


class RouteRecommendationRequest(BaseModel):
    route: str = "standard"
    cost_preference: str = "cost_optimized"
    data_mode: str = "customer"
    max_latency_ms: int = Field(default=LEGAL_READY_LATENCY_MS, ge=250, le=30000)
    count: int = Field(default=3, ge=1, le=5)
    provider_diversity: bool = True
    provider_ids: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(
        default_factory=lambda: ["text_input", "instruction"]
    )

    @field_validator("route")
    @classmethod
    def _valid_route(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"standard", "premium"}:
            raise ValueError("must be standard or premium")
        return value

    @field_validator("cost_preference")
    @classmethod
    def _valid_cost_preference(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"free_only", "cost_optimized", "quality"}:
            raise ValueError("must be free_only, cost_optimized, or quality")
        return value

    @field_validator("data_mode")
    @classmethod
    def _valid_data_mode(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"customer", "strict", "demo"}:
            raise ValueError("must be customer, strict, or demo")
        return value


# ── Helpers ─────────────────────────────────────────────────────────────────


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "…" + key[-4:]


def _parse_uuid(value: str, label: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {value!r}")


def _clean_optional(value: Any) -> str:
    return str(value or "").strip()


def _capacity(value: Any) -> int:
    try:
        parsed = int(value or 100)
    except (TypeError, ValueError):
        parsed = 100
    return max(1, min(parsed, 1000))


def _tokens_per_second(
    output_tokens: int | None, elapsed_ms: int | None
) -> float | None:
    if not output_tokens or not elapsed_ms or elapsed_ms <= 0:
        return None
    return round(output_tokens / (elapsed_ms / 1000), 2)


def _usage_payload(
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    prompt = int(prompt_tokens or 0)
    completion = int(completion_tokens or 0)
    total = int(total_tokens or (prompt + completion))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "tokens_per_second": _tokens_per_second(completion, elapsed_ms),
    }


def _provider_error_evidence(exc: Exception) -> dict[str, Any]:
    """Normalize provider failures without returning response bodies or IDs."""

    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None

    if status == 401:
        category = "invalid_credentials"
        message = "Provider rejected the API credential."
        credential_state = "invalid"
    elif status in (402, 403):
        category = "billing_or_provider_policy"
        message = "Billing or provider policy blocked the canary; credential validity is indeterminate."
        credential_state = "indeterminate_policy_block"
    elif status == 429:
        category = "rate_limited"
        message = "Provider rate or quota limits blocked the canary."
        credential_state = "accepted_but_blocked"
    elif status == 400:
        category = "unsupported_or_bad_request"
        message = "Provider rejected the model or synthetic canary request."
        credential_state = "indeterminate"
    elif status is not None and status >= 500:
        category = "provider_unavailable"
        message = "Provider was unavailable during the canary."
        credential_state = "indeterminate"
    elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        category = "provider_timeout"
        message = "Provider timed out during the canary."
        credential_state = "indeterminate"
    else:
        category = "network_or_provider_error"
        message = "Provider canary failed without a safe diagnostic response."
        credential_state = "indeterminate"
    return {
        "http_status": status,
        "error_category": category,
        "error": message,
        "credential_state": credential_state,
    }


def _route_audit_payload(
    config: dict[str, Any], reload_result: dict[str, Any]
) -> dict[str, Any]:
    def _target_payload(target: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider_id": target.get("provider_id"),
            "key_id": target.get("key_id"),
            "model": target.get("model"),
            "capacity": target.get("capacity"),
            "allow_matter_context": bool(target.get("allow_matter_context")),
            "alternates": [
                _target_payload(alternate) for alternate in target.get("alternates", [])
            ],
            "fallbacks": [
                _target_payload(fallback) for fallback in target.get("fallbacks", [])
            ],
        }

    return {
        "standard": _target_payload(config.get("standard", {})),
        "premium": _target_payload(config.get("premium", {})),
        "litellm_updated": bool(reload_result.get("litellm_updated")),
        "litellm_error": reload_result.get("litellm_error"),
    }


def _model_test_audit_payload(
    *,
    body: RouteTestRequest,
    key: LLMProviderKey,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "route": body.route,
        "provider_id": body.provider_id,
        "key_id": str(key.id),
        "key_hint": key.key_hint,
        "model": body.model,
        "capability": "text",
        "ok": bool(result.get("ok")),
        "credential_state": result.get("credential_state"),
        "model_used": result.get("model_used"),
        "http_status": result.get("http_status"),
        "error_category": result.get("error_category"),
        "provider_latency_ms": result.get("provider_latency_ms"),
        "server_elapsed_ms": result.get("server_elapsed_ms"),
        "prompt_tokens": result.get("prompt_tokens"),
        "completion_tokens": result.get("completion_tokens"),
        "total_tokens": result.get("total_tokens"),
    }


def operator_debug_mode_audit_payload(
    *,
    tenant_id: str,
    conversation_id: str,
    enabled: bool,
    retention_days: int,
    reason: str,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Build the operator-debug audit record from an explicit safe allowlist.

    ``prompt`` is accepted so callers cannot accidentally pass it through via
    an unfiltered dictionary, but its contents are deliberately never logged.
    """
    del prompt
    return {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "enabled": enabled,
        "retention_days": retention_days,
        "reason": reason,
    }


def _is_free_model(model_id: str, item: dict[str, Any], provider_id: str = "") -> bool:
    mid = (model_id or "").lower()
    if provider_id == "opencode-zen" and mid in ZEN_FREE_MODELS:
        return True
    if ":free" in mid or mid.endswith("-free"):
        return True
    pricing = item.get("pricing") if isinstance(item, dict) else None
    if isinstance(pricing, dict):
        prompt = str(pricing.get("prompt", "")).strip()
        completion = str(pricing.get("completion", "")).strip()
        return prompt in {"0", "0.0", "0.000000"} and completion in {
            "0",
            "0.0",
            "0.000000",
        }
    return False


def _confidential_data_unsafe_targets(
    config: dict[str, Any], catalog: dict[str, Any]
) -> list[dict[str, str]]:
    """Return customer routes not affirmatively approved for confidential data.

    OpenCode documents its free Zen endpoints as evaluation capacity whose data
    may be collected or used to improve models. Customer aliases can carry
    privileged or confidential material, so those models remain demo/lab-only.
    Free capacity from providers with an acceptable data policy is not blocked.
    Missing catalog rows and unknown provider terms fail closed: absence of an
    approval is not authorization to transmit privileged client material.
    """

    catalog_rows = catalog.get("models", []) if isinstance(catalog, dict) else []
    catalog_index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in catalog_rows if isinstance(catalog_rows, list) else []:
        if not isinstance(row, dict):
            continue
        provider_id = _clean_optional(row.get("provider_id"))
        model_id = _clean_optional(row.get("id") or row.get("model"))
        if provider_id and model_id:
            catalog_index[(provider_id, model_id)] = row

    blocked: list[dict[str, str]] = []

    def _inspect(route_name: str, placement: str, target: dict[str, Any]) -> None:
        provider_id = _clean_optional(target.get("provider_id"))
        model_id = _clean_optional(target.get("model"))
        if not provider_id or not model_id:
            return
        row = catalog_index.get((provider_id, model_id), {})
        confidential_data_allowed = row.get("confidential_data_allowed")
        is_unsafe_zen_free = provider_id == "opencode-zen" and _is_free_model(
            model_id, row, provider_id
        )
        if confidential_data_allowed is not True or is_unsafe_zen_free:
            blocked.append(
                {
                    "route": route_name,
                    "placement": placement,
                    "provider_id": provider_id,
                    "model": model_id,
                    "data_policy": str(row.get("data_policy") or "unknown"),
                    "reason": (
                        "not_approved"
                        if confidential_data_allowed is None
                        else "disallowed"
                    ),
                }
            )

    for route_name in ("standard", "premium"):
        route = config.get(route_name, {})
        if not isinstance(route, dict):
            continue
        _inspect(route_name, "primary", route)
        for index, alternate in enumerate(route.get("alternates", []) or []):
            if isinstance(alternate, dict):
                _inspect(route_name, f"alternate[{index}]", alternate)
        for index, fallback in enumerate(route.get("fallbacks", []) or []):
            if isinstance(fallback, dict):
                _inspect(route_name, f"fallback[{index}]", fallback)

    return blocked


async def _enforce_customer_route_data_policy(
    request: Request,
    db: AsyncSession,
    config: dict[str, Any],
) -> None:
    """Reject customer aliases whose upstream data terms are not confidential-safe."""

    catalog = await _get_model_catalog(db)
    unsafe_targets = _confidential_data_unsafe_targets(config, catalog)
    if not unsafe_targets:
        return
    await record_operator_audit(
        db,
        request,
        action="llm.routes_activation_blocked",
        resource_type="llm_route_config",
        resource_id=LLM_ROUTE_CONFIG_KEY,
        metadata={
            "reason": CONFIDENTIAL_DATA_BLOCK_CODE,
            "targets": unsafe_targets,
        },
    )
    await db.commit()
    raise HTTPException(
        status_code=409,
        detail={
            "code": CONFIDENTIAL_DATA_BLOCK_CODE,
            "message": (
                "Every customer route target must be affirmatively approved for "
                "confidential legal traffic. Review its upstream terms or use it "
                "only with synthetic or sanitized demo data."
            ),
            "targets": unsafe_targets,
        },
    )


def _provider_api_mode(provider_id: str, model_id: str) -> str:
    """Return the provider endpoint family required by a catalog model."""

    mid = (model_id or "").lower()
    if provider_id == "anthropic":
        return "messages"
    if provider_id not in {"opencode-zen", "opencode-go"}:
        return "chat_completions"
    if mid.startswith(("gpt-", "grok-", "muse-")):
        return "responses"
    if mid.startswith("gemini-"):
        return "google"
    if mid.startswith(("claude-", "qwen")):
        return "messages"
    if provider_id == "opencode-go" and mid.startswith("minimax-"):
        return "messages"
    return "chat_completions"


def _responses_output_text(payload: dict[str, Any]) -> str:
    """Extract text from an OpenAI Responses API payload.

    OpenCode Go exposes GPT, Grok, and Muse through ``/responses`` rather
    than ``/chat/completions``.  Keep this deliberately tolerant of response
    item types so a provider-side metadata item cannot make the canary crash.
    """

    direct_text = payload.get("output_text")
    if isinstance(direct_text, str):
        return direct_text.strip()

    parts: list[str] = []
    for output in payload.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"}:
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return " ".join(parts).strip()


def _route_compatible(provider_id: str, model_id: str) -> bool:
    mode = _provider_api_mode(provider_id, model_id)
    return mode == "chat_completions" or (
        provider_id == "anthropic" and mode == "messages"
    )


def _model_data_policy(
    provider_id: str, model_id: str, is_free: bool
) -> dict[str, Any]:
    if provider_id == "opencode-zen" and is_free:
        return {
            "data_policy": "training_or_improvement_possible",
            "confidential_data_allowed": False,
        }
    # OpenCode documents the Go plan as zero-retention with no model training
    # across its curated provider set, not only for the DeepSeek entries.
    # https://opencode.ai/docs/go/#privacy
    if provider_id == "opencode-go":
        return {
            "data_policy": "zero_retention",
            "confidential_data_allowed": True,
        }
    if provider_id == "openrouter":
        return {
            "data_policy": "zero_retention_enforced",
            "confidential_data_allowed": True,
        }
    return {"data_policy": "provider_terms", "confidential_data_allowed": None}


def _derive_capabilities(item: dict, provider_id: str) -> list[str]:
    """Derive capability tags from model metadata for legal-ops filtering.

    Tags include provider-declared input/output modalities plus legal-chat
    signals such as tool_use, reasoning, research, rag, and large_context.
    """
    caps: set[str] = set()
    model_id = (item.get("id") or "").lower()
    description = (item.get("description") or "").lower()
    supported_parameters = item.get("supported_parameters") or []
    if not isinstance(supported_parameters, list):
        supported_parameters = []
    supported = {str(param).lower() for param in supported_parameters}

    # OpenCode and DeepSeek return intentionally sparse /models rows. Their
    # chat-completions endpoints are still instruction/text interfaces, so add
    # only that safe baseline rather than inventing vision/audio capabilities.
    if _route_compatible(provider_id, model_id) and not any(
        term in model_id
        for term in ("embed", "rerank", "moderation", "audio", "speech", "tts")
    ):
        caps.update({"text_input", "instruction"})

    # 1. Architecture modality (OpenRouter)
    architecture = item.get("architecture") or {}
    if isinstance(architecture, dict):
        modality = (architecture.get("modality") or "").lower()
        input_modalities = {
            str(value).lower() for value in architecture.get("input_modalities") or []
        }
        output_modalities = {
            str(value).lower() for value in architecture.get("output_modalities") or []
        }
        if "->" in modality:
            raw_inputs, raw_outputs = modality.split("->", 1)
            input_modalities.update(raw_inputs.replace(",", "+").split("+"))
            output_modalities.update(raw_outputs.replace(",", "+").split("+"))

        if "text" in input_modalities:
            caps.add("text_input")
        if "file" in input_modalities:
            caps.add("file_input")
        if "audio" in input_modalities:
            caps.add("audio_input")
        if "audio" in output_modalities:
            caps.add("audio_output")
        if "transcription" in output_modalities or (
            "audio" in input_modalities and "text" in output_modalities
        ):
            caps.add("speech_to_text")
        if "embeddings" in output_modalities:
            caps.add("embeddings")
        if "image" in modality:
            caps.add("vision")
        for key in ("input_modalities", "output_modalities"):
            values = architecture.get(key) or []
            if isinstance(values, list) and any(
                "image" in str(value).lower() for value in values
            ):
                caps.add("vision")

    # 2. Model ID patterns (works for all providers)
    if any(kw in model_id for kw in ("vision", "/vl", "-vl", "multimodal", "vl-")):
        caps.add("vision")
    if any(
        kw in model_id for kw in ("instruct", "-it", "_it", "/it", "chat", "assistant")
    ):
        caps.add("instruction")
    if any(
        kw in model_id
        for kw in ("deepseek-reasoner", "reasoner", "reasoning", "thinking", "o1", "o3")
    ):
        caps.add("reasoning")
    if any(
        kw in model_id
        for kw in (
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "claude-3-5-sonnet",
            "claude-3-opus",
            "claude-sonnet-4",
            "claude-haiku-4",
        )
    ):
        caps.add("tool_use")

    # 3. Provider-declared supported parameters
    if supported.intersection(
        {"tools", "tool_choice", "function_call", "functions", "parallel_tool_calls"}
    ):
        caps.add("tool_use")
    if supported.intersection(
        {"response_format", "structured_outputs", "json_schema", "json_object"}
    ):
        caps.add("structured_output")
    if supported.intersection({"reasoning", "include_reasoning"}):
        caps.add("reasoning")
    if supported.intersection({"web_search_options"}):
        caps.add("research")

    # 4. Description keywords (OpenRouter provides rich descriptions)
    if any(
        kw in description
        for kw in (
            "instruction",
            "instruction-tuned",
            "chat model",
            "conversational",
            "assistant",
        )
    ):
        caps.add("instruction")
    if any(
        kw in description
        for kw in (
            "function calling",
            "tool use",
            "tool_use",
            "function call",
            "tools",
            "structured output",
            "json mode",
        )
    ):
        caps.add("tool_use")
    if any(
        kw in description
        for kw in ("rag", "retrieval-augmented", "retrieval", "grounding")
    ):
        caps.add("rag")
    if any(
        kw in description
        for kw in ("reasoning", "chain-of-thought", "cot", "deep reasoning")
    ):
        caps.add("reasoning")
    if any(
        kw in description
        for kw in (
            "search",
            "web search",
            "browsing",
            "web browsing",
            "online",
            "internet",
        )
    ):
        caps.add("research")
    if any(
        kw in description
        for kw in (
            "vision",
            "multimodal",
            "document understanding",
            "pdf",
            "ocr",
            "image recognition",
        )
    ):
        caps.add("vision")
    if any(
        kw in description
        for kw in (
            "legal",
            "law ",
            "litigation",
            "contract",
            "compliance",
            "regulation",
            "statute",
            "court",
            "attorney",
            "counsel",
            "legislation",
            "jurisdiction",
            "case law",
            "legal document",
            "regulatory",
        )
    ):
        caps.add("legal")
    if any(
        kw in description
        for kw in (
            "structured output",
            "json schema",
            "structured generation",
            "json mode",
        )
    ):
        caps.add("structured_output")

    # 5. Context length tiers
    ctx = (
        item.get("context_length")
        or item.get("context_window")
        or item.get("max_context_length")
        or 0
    )
    if isinstance(ctx, (int, float)) and ctx >= 1_000_000:
        caps.add("ultra_context")
        caps.add("large_context")
        caps.add("rag")
    elif isinstance(ctx, (int, float)) and ctx >= 100_000:
        caps.add("large_context")
        caps.add("rag")

    return sorted(caps)


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_latency_ms(item: dict[str, Any]) -> int | None:
    """Read latency from provider/catalog metadata when available."""
    latency = _first_number(
        item.get("latency_ms"),
        item.get("avg_latency_ms"),
        item.get("p50_latency_ms"),
        item.get("response_time_ms"),
        item.get("ttft_ms"),
        item.get("time_to_first_token_ms"),
    )
    if latency is None:
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        latency = _first_number(
            metrics.get("latency_ms"),
            metrics.get("avg_latency_ms"),
            metrics.get("p50_latency_ms"),
            metrics.get("ttft_ms"),
        )
    if latency is None:
        return None
    return int(latency)


def _model_text(*values: Any) -> str:
    return " ".join(str(value or "").lower() for value in values)


def _legal_model_eligibility(
    item: dict[str, Any],
    *,
    model_id: str,
    capabilities: list[str],
    is_free: bool,
    context_length: Any,
    max_completion_tokens: Any,
    latency_ms: int | None,
    route_compatible: bool = True,
) -> dict[str, Any]:
    caps = set(capabilities)
    text = _model_text(model_id, item.get("name"), item.get("description"))
    modality = ""
    architecture = item.get("architecture")
    if isinstance(architecture, dict):
        modality = str(architecture.get("modality") or "").lower()

    exclusion_reasons: list[str] = []
    badges: list[str] = []
    score = 0

    if not route_compatible:
        exclusion_reasons.append("unsupported_api_mode")
    else:
        score += 1

    non_chat_terms = (
        "embedding",
        "embed",
        "rerank",
        "reranker",
        "moderation",
        "audio",
        "speech",
        "tts",
        "stt",
        "image generation",
        "text-to-image",
        "diffusion",
    )
    if any(term in text for term in non_chat_terms):
        exclusion_reasons.append("not_chat_model")
    if modality and "text" not in modality:
        exclusion_reasons.append("not_text_chat")

    coding_terms = (
        "coder",
        "coding",
        "code-",
        "-code",
        "codestral",
        "devstral",
        "programming",
        "software engineering",
    )
    if any(term in text for term in coding_terms):
        exclusion_reasons.append("coding_specialized")

    ctx = _first_number(context_length)
    if ctx is None:
        score += 1
    elif ctx < MIN_LEGAL_CONTEXT_LENGTH:
        exclusion_reasons.append("low_context")
    elif ctx >= RECOMMENDED_LEGAL_CONTEXT_LENGTH:
        score += 2
        badges.append("Document-capable")
    else:
        score += 1

    completion = _first_number(max_completion_tokens)
    if completion is not None and completion < 2048:
        exclusion_reasons.append("low_output_limit")

    if latency_ms is not None:
        if latency_ms > LEGAL_READY_LATENCY_MS:
            exclusion_reasons.append("slow_latency")
        else:
            score += 2
            badges.append("Fast")

    if "instruction" in caps:
        score += 2
    else:
        exclusion_reasons.append("not_instruction_tuned")

    preferred_caps = {
        "reasoning",
        "rag",
        "large_context",
        "ultra_context",
        "structured_output",
        "vision",
        "legal",
        "tool_use",
    }
    score += min(5, len(caps.intersection(preferred_caps)))
    if caps.intersection(
        {"reasoning", "rag", "large_context", "ultra_context", "structured_output"}
    ):
        badges.append("Legal-ready")
    if "vision" in caps:
        badges.append("Document-capable")

    if exclusion_reasons:
        legal_tier = "excluded"
        legal_eligible = False
    elif score >= 6:
        legal_tier = "recommended"
        legal_eligible = True
    elif score >= 4:
        legal_tier = "usable"
        legal_eligible = True
    else:
        legal_tier = "limited"
        legal_eligible = False
        exclusion_reasons.append("insufficient_legal_signals")

    if legal_eligible and "Legal-ready" not in badges:
        badges.append("Legal-ready")

    return {
        "legal_eligible": legal_eligible,
        "legal_tier": legal_tier,
        "legal_score": score,
        "eligibility_badges": list(dict.fromkeys(badges)),
        "exclusion_reasons": list(dict.fromkeys(exclusion_reasons)),
        "latency_ms": latency_ms,
        "latency_eligible": latency_ms is None or latency_ms <= LEGAL_READY_LATENCY_MS,
        "latency_threshold_ms": LEGAL_READY_LATENCY_MS,
    }


def _normalize_model_item(item: Any, provider_id: str) -> dict | None:
    if isinstance(item, str):
        item = {"id": item, "name": item}
    if not isinstance(item, dict):
        return None
    mid = item.get("id") or item.get("model_id") or item.get("name") or ""
    mid = str(mid).strip()
    if not mid:
        return None
    architecture = item.get("architecture") if isinstance(item, dict) else None
    top_provider = item.get("top_provider") if isinstance(item, dict) else None
    ctx = (
        item.get("context_length")
        or item.get("context_window")
        or item.get("max_context_length")
    )
    max_completion_tokens = (
        top_provider.get("max_completion_tokens")
        if isinstance(top_provider, dict)
        else item.get("max_completion_tokens")
    )
    capabilities = _derive_capabilities(item, provider_id)
    is_free = _is_free_model(mid, item, provider_id)
    api_mode = _provider_api_mode(provider_id, mid)
    route_compatible = _route_compatible(provider_id, mid)
    latency_ms = _extract_latency_ms(item)
    eligibility = _legal_model_eligibility(
        item,
        model_id=mid,
        capabilities=capabilities,
        is_free=is_free,
        context_length=ctx,
        max_completion_tokens=max_completion_tokens,
        latency_ms=latency_ms,
        route_compatible=route_compatible,
    )
    data_policy = _model_data_policy(provider_id, mid, is_free)
    return {
        "id": mid,
        "name": item.get("name") or mid,
        "provider_id": provider_id,
        "description": item.get("description"),
        "context_length": ctx,
        "pricing": (
            item.get("pricing") if isinstance(item.get("pricing"), dict) else None
        ),
        "is_free": is_free,
        "economic_tier": "free" if is_free else "paid",
        "api_mode": api_mode,
        "route_compatible": route_compatible,
        "modality": (
            architecture.get("modality") if isinstance(architecture, dict) else None
        ),
        "max_completion_tokens": max_completion_tokens,
        "supported_parameters": (
            item.get("supported_parameters")
            if isinstance(item.get("supported_parameters"), list)
            else []
        ),
        "capabilities": capabilities,
        **data_policy,
        **eligibility,
    }


def _hydrate_legal_eligibility(model: dict[str, Any]) -> dict[str, Any]:
    """Recompute eligibility for saved rows as catalog policy evolves."""
    hydrated = dict(model)
    provider_id = str(hydrated.get("provider_id") or "")
    model_id = str(hydrated.get("id") or "")
    capabilities = sorted(
        set(hydrated.get("capabilities") or []).union(
            _derive_capabilities(hydrated, provider_id)
        )
    )
    is_free = _is_free_model(model_id, hydrated, provider_id)
    api_mode = _provider_api_mode(provider_id, model_id)
    route_compatible = _route_compatible(provider_id, model_id)
    eligibility = _legal_model_eligibility(
        hydrated,
        model_id=model_id,
        capabilities=capabilities,
        is_free=is_free,
        context_length=hydrated.get("context_length"),
        max_completion_tokens=hydrated.get("max_completion_tokens"),
        latency_ms=_extract_latency_ms(hydrated),
        route_compatible=route_compatible,
    )
    hydrated.update(
        {
            "capabilities": capabilities,
            "is_free": is_free,
            "economic_tier": "free" if is_free else "paid",
            "api_mode": api_mode,
            "route_compatible": route_compatible,
            **_model_data_policy(provider_id, model_id, is_free),
        }
    )
    hydrated.update(eligibility)
    return hydrated


def _merge_catalog_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate provider/model rows while preserving every key choice."""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for model in models:
        provider_id = str(model.get("provider_id") or "")
        model_id = str(model.get("id") or "")
        if not provider_id or not model_id:
            continue
        ident = (provider_id, model_id)
        key_ids = [str(value) for value in model.get("key_ids", []) if value]
        if model.get("key_id"):
            key_ids.append(str(model["key_id"]))
        key_names = [str(value) for value in model.get("key_names", []) if value]
        if model.get("key_name"):
            key_names.append(str(model["key_name"]))

        if ident not in merged:
            merged[ident] = dict(model)
            merged[ident]["key_ids"] = list(dict.fromkeys(key_ids))
            merged[ident]["key_names"] = list(dict.fromkeys(key_names))
            continue

        current = merged[ident]
        current["key_ids"] = list(
            dict.fromkeys([*(current.get("key_ids") or []), *key_ids])
        )
        current["key_names"] = list(
            dict.fromkeys([*(current.get("key_names") or []), *key_names])
        )
        current["is_new"] = bool(current.get("is_new") or model.get("is_new"))

    for model in merged.values():
        key_ids = model.get("key_ids") or []
        key_names = model.get("key_names") or []
        model["key_count"] = len(key_ids)
        if key_ids:
            model["key_id"] = key_ids[0]
        if len(key_names) == 1:
            model["key_name"] = key_names[0]
        elif key_names:
            model["key_name"] = f"{len(key_names)} stored keys"
    return list(merged.values())


def _hydrate_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(catalog or {})
    models = _merge_catalog_models(
        [
            _hydrate_legal_eligibility(model)
            for model in hydrated.get("models", [])
            if isinstance(model, dict)
        ]
    )
    hydrated["models"] = models
    hydrated["model_count"] = len(models)
    hydrated["free_count"] = sum(1 for model in models if model.get("is_free"))
    hydrated["free_legal_count"] = sum(
        1 for model in models if model.get("is_free") and model.get("legal_eligible")
    )
    hydrated["recommended_count"] = sum(
        1 for model in models if model.get("legal_tier") == "recommended"
    )
    hydrated["excluded_count"] = sum(
        1 for model in models if model.get("legal_tier") == "excluded"
    )
    hydrated["new_count"] = sum(1 for model in models if model.get("is_new"))
    return hydrated


def _canary_failure_is_current(health: dict[str, Any], now: datetime) -> bool:
    if health.get("ok"):
        return False
    tested_at = health.get("tested_at")
    if not isinstance(tested_at, datetime):
        return False
    category = str(health.get("error_category") or "")
    ttl = {
        "invalid_credentials": timedelta(hours=24),
        "billing_or_provider_policy": timedelta(hours=24),
        "rate_limited": timedelta(minutes=15),
        "provider_unavailable": timedelta(minutes=5),
        "provider_timeout": timedelta(minutes=5),
        "unexpected_response": timedelta(hours=1),
    }.get(category, timedelta(minutes=15))
    return tested_at >= now - ttl


def _recommend_route_targets(
    *,
    catalog: dict[str, Any],
    keys: list[dict[str, str]],
    health: dict[tuple[str, str, str], dict[str, Any]],
    criteria: RouteRecommendationRequest,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Rank catalog models into a primary plus ordered runtime fallbacks."""

    current_time = now or datetime.now(timezone.utc)
    keys_by_id = {str(key["id"]): key for key in keys}
    provider_scope = set(criteria.provider_ids)
    required = set(criteria.required_capabilities)
    ranked: list[dict[str, Any]] = []

    for model in catalog.get("models", []):
        if not isinstance(model, dict):
            continue
        provider_id = str(model.get("provider_id") or "")
        model_id = str(model.get("id") or "")
        if not provider_id or not model_id:
            continue
        if provider_scope and provider_id not in provider_scope:
            continue
        if model.get("route_compatible") is False:
            continue
        if not model.get("legal_eligible") or model.get("legal_tier") == "excluded":
            continue
        confidential = model.get("confidential_data_allowed")
        if criteria.data_mode in {"strict", "customer"} and confidential is not True:
            continue
        if criteria.cost_preference == "free_only" and not model.get("is_free"):
            continue
        capabilities = set(model.get("capabilities") or [])
        if not required.issubset(capabilities):
            continue
        latency_ms = _extract_latency_ms(model)
        if latency_ms is not None and latency_ms > criteria.max_latency_ms:
            continue

        candidate_key_ids = [
            str(key_id)
            for key_id in (model.get("key_ids") or [model.get("key_id")])
            if key_id
            and str(key_id) in keys_by_id
            and keys_by_id[str(key_id)].get("provider_id") == provider_id
        ]
        if not candidate_key_ids:
            continue

        def _key_rank(key_id: str) -> tuple[int, float]:
            result = health.get((provider_id, model_id, key_id))
            if not result:
                return (1, 0)
            tested_at = result.get("tested_at")
            timestamp = tested_at.timestamp() if isinstance(tested_at, datetime) else 0
            if result.get("ok"):
                return (2, timestamp)
            if _canary_failure_is_current(result, current_time):
                return (0, timestamp)
            return (1, timestamp)

        candidate_key_ids.sort(key=_key_rank, reverse=True)
        key_id = candidate_key_ids[0]
        selected_health = health.get((provider_id, model_id, key_id))
        if selected_health and _canary_failure_is_current(
            selected_health, current_time
        ):
            # Every available key for this model must be currently failing before
            # the model is removed. A second, untested key remains eligible.
            healthy_or_unknown = [
                value
                for value in candidate_key_ids
                if not (
                    health.get((provider_id, model_id, value))
                    and _canary_failure_is_current(
                        health[(provider_id, model_id, value)], current_time
                    )
                )
            ]
            if not healthy_or_unknown:
                continue
            key_id = healthy_or_unknown[0]
            selected_health = health.get((provider_id, model_id, key_id))

        tier_score = {"recommended": 500, "usable": 350, "limited": 100}.get(
            str(model.get("legal_tier") or ""), 0
        )
        score = tier_score + int(model.get("legal_score") or 0) * 10
        reasons = [str(model.get("legal_tier") or "usable").replace("_", " ")]
        if confidential is True:
            score += 80
            reasons.append("confidential-data approved")
        elif confidential is None:
            reasons.append("provider terms require review")
        else:
            score -= 30
            reasons.append("synthetic/demo data only")
        if selected_health and selected_health.get("ok"):
            score += 250
            reasons.append("recent canary passed")
        else:
            reasons.append("canary not recently passed")
        if latency_ms is not None:
            score += max(0, 100 - int(latency_ms / 30))
            reasons.append(f"{latency_ms}ms catalog latency")
        if criteria.cost_preference == "free_only":
            score += 100
        elif criteria.cost_preference == "cost_optimized":
            score += 100 if model.get("is_free") else 20
        elif not model.get("is_free"):
            score += 60

        model_text = f"{model_id} {model.get('name') or ''}".lower()
        if criteria.route == "premium":
            if any(term in model_text for term in ("pro", "sonnet", "opus", "ultra")):
                score += 60
                reasons.append("premium-quality family")
            if "flash" in model_text:
                score -= 10
        else:
            if any(term in model_text for term in ("flash", "mini", "haiku", "gemma")):
                score += 40
                reasons.append("standard-efficiency family")

        ranked.append(
            {
                "key_id": key_id,
                "key_name": keys_by_id[key_id].get("name"),
                "provider_id": provider_id,
                "provider_name": model.get("provider_name") or provider_id,
                "model": model_id,
                "model_name": model.get("name") or model_id,
                "capacity": 100,
                "score": score,
                "is_free": bool(model.get("is_free")),
                "legal_tier": model.get("legal_tier"),
                "data_policy": model.get("data_policy"),
                "confidential_data_allowed": confidential,
                "latency_ms": latency_ms,
                "canary_ok": bool(selected_health and selected_health.get("ok")),
                "canary_tested_at": (
                    selected_health["tested_at"].isoformat()
                    if selected_health
                    and isinstance(selected_health.get("tested_at"), datetime)
                    else None
                ),
                "reasons": reasons,
            }
        )

    ranked.sort(key=lambda item: (-item["score"], item["provider_id"], item["model"]))
    selected: list[dict[str, Any]] = []
    if criteria.provider_diversity:
        seen_providers: set[str] = set()
        for candidate in ranked:
            if candidate["provider_id"] in seen_providers:
                continue
            selected.append(candidate)
            seen_providers.add(candidate["provider_id"])
            if len(selected) >= criteria.count:
                break
    for candidate in ranked:
        if len(selected) >= criteria.count:
            break
        if candidate not in selected:
            selected.append(candidate)

    warnings: list[str] = []
    if len(selected) < criteria.count:
        warnings.append(
            f"Only {len(selected)} model(s) met the current criteria; requested {criteria.count}."
        )
    if any(item["confidential_data_allowed"] is None for item in selected):
        warnings.append(
            "One or more selected providers still require a data-terms review."
        )
    if any(not item["canary_ok"] for item in selected):
        warnings.append(
            "Test every selected target before activation; some lack a recent passing canary."
        )

    return {
        "route": criteria.route,
        "criteria": criteria.model_dump(),
        "candidates": selected,
        "eligible_count": len(ranked),
        "warnings": warnings,
    }


def _litellm_model_name(provider_id: str, model: str) -> str:
    model = _clean_optional(model)
    mode = _PRESET_BY_ID.get(provider_id, {}).get("litellm_mode", "openai_compatible")
    if not model:
        return model
    if mode == "anthropic":
        return model if model.startswith("anthropic/") else f"anthropic/{model}"
    if mode == "openrouter":
        return model if model.startswith("openrouter/") else f"openrouter/{model}"
    return model if model.startswith("openai/") else f"openai/{model}"


async def _get_route_config(db: AsyncSession) -> dict:
    result = await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == LLM_ROUTE_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    if row and row.value:
        return row.value
    return {"standard": {}, "premium": {}}


def _profile_payload(profile: LLMRoutingProfile) -> dict[str, Any]:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "description": profile.description,
        "is_default": profile.is_default,
        "is_active": profile.is_active,
        "standard_allow_matter_context": profile.standard_allow_matter_context,
        "premium_allow_matter_context": profile.premium_allow_matter_context,
        "standard": {
            **dict(profile.standard_route or {}),
            "allow_matter_context": profile.standard_allow_matter_context,
        },
        "premium": {
            **dict(profile.premium_route or {}),
            "allow_matter_context": profile.premium_allow_matter_context,
        },
        "activation": profile.activation or {},
    }


async def _selected_profile(
    db: AsyncSession, profile_id: str | None
) -> LLMRoutingProfile | None:
    if not profile_id:
        return None
    try:
        parsed = uuid.UUID(profile_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid routing profile id")
    profile = await db.get(LLMRoutingProfile, parsed)
    if profile is None:
        raise HTTPException(status_code=404, detail="Routing profile not found")
    return profile


async def _save_route_config(db: AsyncSession, config: dict) -> None:
    result = await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == LLM_ROUTE_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = PlatformSetting(key=LLM_ROUTE_CONFIG_KEY, value=config)
        db.add(row)
    else:
        row.value = config
        row.updated_at = datetime.now(timezone.utc)
    await db.flush()


def _build_litellm_model_entry(
    alias: str,
    provider_id: str,
    model: str,
    plaintext_key: str,
    *,
    capacity: int | None = None,
    deployment_id: str | None = None,
) -> dict:
    preset = _PRESET_BY_ID.get(provider_id, {})
    mode = preset.get("litellm_mode", "openai_compatible")
    entry: dict[str, Any] = {
        "model_name": alias,
        "litellm_params": {
            "model": _litellm_model_name(provider_id, model),
            "api_key": plaintext_key,
        },
    }
    if mode == "openai_compatible" and preset.get("base_url"):
        entry["litellm_params"]["api_base"] = preset["base_url"]
    if provider_id == "openrouter":
        entry["litellm_params"]["extra_body"] = {
            "provider": dict(OPENROUTER_CONFIDENTIAL_PROVIDER_PREFERENCES)
        }
    if capacity:
        entry["litellm_params"]["weight"] = _capacity(capacity)
    if deployment_id:
        entry["model_info"] = {
            "id": deployment_id,
            "legalapp_managed": True,
        }
    return entry


def _litellm_model_payload(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_name": entry["model_name"],
        "litellm_params": dict(entry.get("litellm_params") or {}),
        "model_info": dict(entry.get("model_info") or {}),
    }


def _litellm_model_items(payload: Any) -> list[dict[str, Any]]:
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _route_revision(config: dict[str, Any]) -> str:
    """Return a stable revision for the provider/key/model route graph."""

    material = {
        route_name: config.get(route_name, {}) or {}
        for route_name in ("standard", "premium")
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def _managed_route_aliases(config: dict[str, Any]) -> dict[str, str]:
    revision = _route_revision(config)
    return {
        "standard": f"clarity-standard-r{revision}",
        "premium": f"clarity-premium-r{revision}",
    }


def _managed_deployment_id(
    alias: str,
    *,
    placement: str,
    key_id: str,
    provider_id: str,
    model: str,
) -> str:
    material = ":".join((alias, placement, key_id, provider_id, model))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://lawhand.ai/litellm/{material}"))


def _litellm_static_alias_matches(
    existing: dict[str, Any], desired: dict[str, Any]
) -> bool:
    existing_params = existing.get("litellm_params") or {}
    desired_params = desired.get("litellm_params") or {}
    return existing_params.get("model") == desired_params.get("model")


def _litellm_error_detail(resp: httpx.Response) -> str:
    return (resp.text or "").strip()[:300]


_CANARY_STRIP_RE = re.compile(r"[^a-z]")


def canary_answer_matches(text: str | None) -> bool:
    """Return whether a canary reply is the expected acknowledgement.

    The canary proves reachability and instruction-following, not formatting
    obedience. Providers legitimately return ``OK.``, ``"OK"``, ``ok``, or wrap
    the token in a short sentence, and reasoning models often prefix a summary
    line. Requiring a byte-exact ``OK`` turned those healthy routes into
    operator-visible failures, so compare on letters only.
    """

    normalized = _CANARY_STRIP_RE.sub("", (text or "").casefold())
    return normalized == "ok"


def _canary_error_message(canary_ok: bool, drained: bool) -> str | None:
    """Describe a canary outcome so an operator knows what to change."""

    if canary_ok:
        return None
    if drained:
        return (
            "Provider reached, but the model spent its entire "
            f"{PROVIDER_CANARY_MAX_TOKENS}-token canary budget on reasoning and "
            "returned no visible text. Raise the canary budget for this model."
        )
    return "Provider responded, but the synthetic canary value was incorrect."


def _canary_error_category(canary_ok: bool, drained: bool) -> str | None:
    if canary_ok:
        return None
    return "reasoning_budget_exhausted" if drained else "unexpected_response"


def _canary_reasoning_drain(payload: Any, content: str) -> bool:
    """Detect a reasoning model that spent the whole budget before answering.

    A ``length``-truncated response with no visible content is a budget
    problem, not an unreachable provider. Naming it separately keeps operators
    from retiring a working route.
    """

    if content:
        return False
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        finish_reason = choices[0].get("finish_reason")
        if finish_reason in {"length", "max_tokens"}:
            return True
        message = choices[0].get("message")
        if isinstance(message, dict) and message.get("reasoning_content"):
            return True
    if isinstance(payload, dict) and payload.get("stop_reason") == "max_tokens":
        return True
    return False


async def _call_litellm_config_update(
    new_model_list: list[dict], fallbacks: list[dict]
) -> tuple[bool, str | None]:
    """Hot-reload LiteLLM aliases with current model-management endpoints."""
    if not settings.LITELLM_BASE_URL or not settings.LITELLM_API_KEY:
        return False, "LiteLLM base URL or API key is not configured"
    base_url = settings.LITELLM_BASE_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.LITELLM_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            info_resp = await client.get(f"{base_url}/model/info", headers=headers)
            if info_resp.status_code not in (200, 204):
                detail = _litellm_error_detail(info_resp)
                return (
                    False,
                    f"LiteLLM /model/info returned {info_resp.status_code}: {detail}",
                )
            existing_items = _litellm_model_items(info_resp.json())
            existing_by_id = {
                str((item.get("model_info") or {}).get("id")): item
                for item in existing_items
                if (item.get("model_info") or {}).get("id")
            }
            existing_by_name: dict[str, list[dict[str, Any]]] = {}
            for item in existing_items:
                item_name = str(item.get("model_name") or "").strip()
                if item_name:
                    existing_by_name.setdefault(item_name, []).append(item)

            for entry in new_model_list:
                name = str(entry.get("model_name") or "").strip()
                if not name:
                    return False, "LiteLLM model entry is missing model_name"
                payload = _litellm_model_payload(entry)
                desired_id = str((payload.get("model_info") or {}).get("id") or "")
                existing = existing_by_id.get(desired_id) if desired_id else None
                if existing is None and not desired_id:
                    candidates = existing_by_name.get(name, [])
                    existing = next(
                        (
                            candidate
                            for candidate in candidates
                            if _litellm_static_alias_matches(candidate, entry)
                        ),
                        candidates[0] if candidates else None,
                    )
                if existing:
                    model_info = existing.get("model_info") or {}
                    if not model_info.get("db_model"):
                        if _litellm_static_alias_matches(existing, entry):
                            continue
                        return (
                            False,
                            (
                                f"LiteLLM alias {name} is file-backed and differs "
                                "from the saved route; update the LiteLLM config "
                                "file or convert the alias before reloading"
                            ),
                        )
                    model_id = str(model_info.get("id") or "").strip()
                    if not model_id:
                        return False, f"LiteLLM DB-backed alias {name} has no model id"
                    resp = await client.patch(
                        f"{base_url}/model/{model_id}/update",
                        headers=headers,
                        json=payload,
                    )
                else:
                    resp = await client.post(
                        f"{base_url}/model/new",
                        headers=headers,
                        json=payload,
                    )

                if resp.status_code not in (200, 201, 204):
                    detail = _litellm_error_detail(resp)
                    logger.warning(
                        "LiteLLM model upsert for %s returned %s: %s",
                        name,
                        resp.status_code,
                        detail[:200],
                    )
                    return (
                        False,
                        f"LiteLLM model upsert for {name} returned {resp.status_code}: {detail}",
                    )

            router_settings: dict[str, Any] = {
                "routing_strategy": "cost-based-routing",
                # Always send the field so removing the final fallback clears the
                # old router value instead of leaving a stale chain active.
                "fallbacks": fallbacks,
            }
            resp = await client.post(
                f"{base_url}/config/update",
                headers=headers,
                json={"router_settings": router_settings},
            )
            if resp.status_code in (200, 204):
                return True, None
            detail = _litellm_error_detail(resp)
            logger.warning(
                "LiteLLM /config/update returned %s: %s",
                resp.status_code,
                detail[:200],
            )
            return (
                False,
                f"LiteLLM /config/update returned {resp.status_code}: {detail}",
            )
    except Exception as exc:
        logger.warning("LiteLLM config update failed: %s", exc)
        return False, f"LiteLLM config update failed: {exc}"


def _fallback_count(fallback_settings: list[dict]) -> int:
    count = 0
    for item in fallback_settings:
        if not item:
            continue
        count += len(next(iter(item.values())))
    return count


def _build_litellm_reload_payload(
    config: dict[str, Any],
    keys_by_id: dict[str, LLMProviderKey],
    aliases: dict[str, str] | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    """Build LiteLLM model_list/fallbacks from saved route config."""
    new_models: list[dict] = []
    fallback_settings: list[dict] = []
    errors: list[str] = []

    aliases = aliases or _managed_route_aliases(config)

    def _add_model(alias: str, route_dict: dict, label: str, *, placement: str) -> bool:
        kid = route_dict.get("key_id")
        model = route_dict.get("model", "")
        pid = route_dict.get("provider_id", "")
        if not kid or not model or not pid:
            return False
        key = keys_by_id.get(kid)
        if not key:
            errors.append(f"{label}: selected provider key no longer exists")
            return False
        if key.provider_id != pid:
            errors.append(
                f"{label}: selected key belongs to {key.provider_id}, not {pid}"
            )
            return False
        try:
            plaintext = decrypt_token(key.encrypted_key)
        except Exception:
            logger.warning("Failed to decrypt key %s for LiteLLM update", kid)
            errors.append(f"{label}: key decryption failed")
            return False
        new_models.append(
            _build_litellm_model_entry(
                alias,
                pid,
                model,
                plaintext,
                capacity=route_dict.get("capacity"),
                deployment_id=_managed_deployment_id(
                    alias,
                    placement=placement,
                    key_id=str(kid),
                    provider_id=str(pid),
                    model=str(model),
                ),
            )
        )
        return True

    for route_name in ("standard", "premium"):
        route = config.get(route_name, {}) or {}
        alias = aliases[route_name]

        _add_model(alias, route, f"{route_name} primary", placement="primary")
        for i, alternate in enumerate(route.get("alternates", []) or []):
            _add_model(
                alias,
                alternate,
                f"{route_name} balanced target {i + 1}",
                placement=f"alternate-{i}",
            )

        fallback_aliases: list[str] = []
        for i, fallback in enumerate(route.get("fallbacks", []) or []):
            fallback_alias = f"{alias}-fb-{i}"
            if _add_model(
                fallback_alias,
                fallback,
                f"{route_name} fallback {i + 1}",
                placement=f"fallback-{i}",
            ):
                fallback_aliases.append(fallback_alias)
        if fallback_aliases:
            fallback_settings.append({alias: fallback_aliases})

    return new_models, fallback_settings, errors


async def _reload_litellm_routes(
    config: dict[str, Any],
    keys_by_id: dict[str, LLMProviderKey],
    *,
    aliases: dict[str, str] | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    aliases = aliases or _managed_route_aliases(config)
    new_models, fallback_settings, build_errors = _build_litellm_reload_payload(
        config, keys_by_id, aliases
    )
    litellm_updated = False
    litellm_error: str | None = None

    validation: dict[str, Any] = {}
    if build_errors:
        litellm_error = "; ".join(build_errors)
    elif new_models:
        litellm_updated, litellm_error = await _call_litellm_config_update(
            new_models, fallback_settings
        )
        if litellm_updated and validate:
            valid, validation, validation_error = await _probe_litellm_aliases(aliases)
            if not valid:
                litellm_updated = False
                litellm_error = validation_error
    else:
        litellm_error = (
            "No complete provider/key/model targets were available to register"
        )

    return {
        "litellm_updated": litellm_updated,
        "litellm_error": litellm_error,
        "models_registered": len(new_models),
        "fallbacks_registered": _fallback_count(fallback_settings),
        "build_errors": build_errors,
        "app_aliases": aliases,
        "validation": validation,
    }


async def _probe_litellm_aliases(
    aliases: dict[str, str],
) -> tuple[bool, dict[str, Any], str | None]:
    """Prove each candidate alias can serve a real synthetic completion."""

    if not settings.LITELLM_BASE_URL or not settings.LITELLM_API_KEY:
        return False, {}, "LiteLLM base URL or API key is not configured"
    base_url = settings.LITELLM_BASE_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.LITELLM_API_KEY}"}
    results: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            for route_name, alias in aliases.items():
                started = time.monotonic()
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": alias,
                        "messages": [
                            {
                                "role": "system",
                                "content": "Follow the user's output format exactly.",
                            },
                            {"role": "user", "content": "Reply with exactly OK"},
                        ],
                        "temperature": 0,
                        "max_tokens": ROUTE_ACTIVATION_CANARY_MAX_TOKENS,
                    },
                )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if response.status_code != 200:
                    detail = _litellm_error_detail(response)
                    results[route_name] = {
                        "alias": alias,
                        "ok": False,
                        "latency_ms": elapsed_ms,
                        "status_code": response.status_code,
                    }
                    return (
                        False,
                        results,
                        f"Synthetic {route_name} completion failed ({response.status_code}): {detail}",
                    )
                payload = response.json()
                choices = payload.get("choices") or []
                content = (
                    ((choices[0].get("message") or {}).get("content") or "").strip()
                    if choices and isinstance(choices[0], dict)
                    else ""
                )
                if not content:
                    results[route_name] = {
                        "alias": alias,
                        "ok": False,
                        "latency_ms": elapsed_ms,
                        "model": payload.get("model"),
                        "reasoning_drain": _canary_reasoning_drain(payload, content),
                    }
                    if results[route_name]["reasoning_drain"]:
                        return (
                            False,
                            results,
                            (
                                f"Synthetic {route_name} completion spent its entire "
                                f"{ROUTE_ACTIVATION_CANARY_MAX_TOKENS}-token budget on "
                                "reasoning and returned no visible text. The route "
                                "reached the provider; raise "
                                "ROUTE_ACTIVATION_CANARY_MAX_TOKENS for this model."
                            ),
                        )
                    return (
                        False,
                        results,
                        f"Synthetic {route_name} completion returned no text",
                    )
                results[route_name] = {
                    "alias": alias,
                    "ok": True,
                    "latency_ms": elapsed_ms,
                    "model": payload.get("model"),
                }
    except Exception as exc:
        return False, results, f"LiteLLM synthetic completion failed: {str(exc)[:300]}"
    return True, results, None


async def _check_litellm_gateway(
    expected_aliases: list[str] | None = None,
) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    base_url = (settings.LITELLM_BASE_URL or "").rstrip("/")
    expected_aliases = expected_aliases or [
        alias
        for alias in (settings.LITELLM_STANDARD_MODEL, settings.LITELLM_PREMIUM_MODEL)
        if alias
    ]
    aliases = dict.fromkeys(expected_aliases, False)
    status: dict[str, Any] = {
        "status": "disabled" if not settings.LITELLM_ENABLED else "unknown",
        "enabled": settings.LITELLM_ENABLED,
        "base_url": base_url,
        "api_key_configured": bool(settings.LITELLM_API_KEY),
        "checked_at": checked_at,
        "expected_aliases": expected_aliases,
        "latency_ms": None,
        "models_count": None,
        "aliases": aliases,
        "detail": None,
    }
    if not settings.LITELLM_ENABLED:
        status["detail"] = "LITELLM_ENABLED is false"
        return status
    if not base_url:
        status.update({"status": "degraded", "detail": "LITELLM_BASE_URL is empty"})
        return status

    headers = (
        {"Authorization": f"Bearer {settings.LITELLM_API_KEY}"}
        if settings.LITELLM_API_KEY
        else {}
    )
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            live_resp = await client.get(
                f"{base_url}/health/liveliness", headers=headers
            )
            if live_resp.status_code == 404:
                live_resp = await client.get(f"{base_url}/health", headers=headers)
            live_resp.raise_for_status()
            status["latency_ms"] = int((time.monotonic() - started) * 1000)
            status["status"] = "online"

            try:
                models_resp = await client.get(f"{base_url}/models", headers=headers)
                models_resp.raise_for_status()
                payload = models_resp.json()
                items = payload.get("data") if isinstance(payload, dict) else payload
                if not isinstance(items, list):
                    items = []
                names = {
                    str(
                        item.get("id")
                        or item.get("model_name")
                        or item.get("model")
                        or ""
                    )
                    for item in items
                    if isinstance(item, dict)
                }
                status["models_count"] = len(names)
                status["aliases"] = {alias: alias in names for alias in aliases}
                if aliases and not all(status["aliases"].values()):
                    status["status"] = "degraded"
                    missing = [
                        alias
                        for alias, present in status["aliases"].items()
                        if not present
                    ]
                    status["detail"] = (
                        f"Missing alias registration: {', '.join(missing)}"
                    )
            except Exception as model_exc:
                status["status"] = "degraded"
                status["models_error"] = str(model_exc)[:300]
    except Exception as exc:
        status["latency_ms"] = int((time.monotonic() - started) * 1000)
        status["status"] = "offline"
        status["detail"] = str(exc)[:300]
    return status


async def _fetch_models_from_provider(
    base_url: str, models_url: str, plaintext_key: str, provider_id: str
) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                models_url,
                headers={"Authorization": f"Bearer {plaintext_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                items = data.get("data") or data.get("models") or []
            else:
                items = data
            models = []
            for item in items:
                model = _normalize_model_item(item, provider_id)
                if model:
                    models.append(model)
            return sorted(models, key=lambda m: m["id"])
    except Exception as exc:
        logger.warning("Model fetch failed from %s: %s", models_url, exc)
        raise


def _route_aliases(route_name: str, route_dict: dict) -> tuple[str, list[str]]:
    primary = f"clarity-{route_name}"
    fallback_aliases = [
        f"{primary}-fb-{idx}"
        for idx, fallback in enumerate(route_dict.get("fallbacks", []))
        if _clean_optional(fallback.get("key_id"))
        and _clean_optional(fallback.get("provider_id"))
        and _clean_optional(fallback.get("model"))
    ]
    return primary, fallback_aliases


def _target_is_complete(target: dict[str, Any]) -> bool:
    return bool(
        _clean_optional(target.get("key_id"))
        and _clean_optional(target.get("provider_id"))
        and _clean_optional(target.get("model"))
    )


async def _get_model_catalog(db: AsyncSession) -> dict:
    result = await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == LLM_MODEL_CATALOG_KEY)
    )
    row = result.scalar_one_or_none()
    if row and row.value:
        return _hydrate_catalog(row.value)
    return {"models": [], "last_refreshed_at": None}


async def _save_model_catalog(db: AsyncSession, catalog: dict) -> None:
    result = await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == LLM_MODEL_CATALOG_KEY)
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = PlatformSetting(key=LLM_MODEL_CATALOG_KEY, value=catalog)
        db.add(row)
    else:
        row.value = catalog
        row.updated_at = datetime.now(timezone.utc)
    await db.flush()


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/providers")
async def list_providers(request: Request):
    _require_platform_key(request)
    return {"providers": PROVIDER_PRESETS}


@router.get("/provider-keys")
async def list_provider_keys(request: Request, db: AsyncSession = Depends(get_db)):
    _require_platform_key(request)
    result = await db.execute(
        select(LLMProviderKey).order_by(LLMProviderKey.created_at)
    )
    keys = result.scalars().all()
    return {
        "keys": [
            {
                "id": str(k.id),
                "name": k.name,
                "provider_id": k.provider_id,
                "key_hint": k.key_hint,
                "created_at": k.created_at.isoformat(),
            }
            for k in keys
        ]
    }


@router.post("/provider-keys")
async def add_provider_key(
    request: Request,
    body: ProviderKeyCreate,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)
    if body.provider_id not in _PRESET_BY_ID:
        raise HTTPException(
            status_code=400, detail=f"Unknown provider_id: {body.provider_id}"
        )
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key must not be empty")

    hint = body.api_key.strip()[-4:] if len(body.api_key.strip()) >= 4 else "****"
    try:
        encrypted = encrypt_token(body.api_key.strip())
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Key encryption failed — TOKEN_ENCRYPTION_KEY not configured",
        )

    key = LLMProviderKey(
        name=body.name.strip(),
        provider_id=body.provider_id,
        encrypted_key=encrypted,
        key_hint=hint,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return {
        "id": str(key.id),
        "name": key.name,
        "provider_id": key.provider_id,
        "key_hint": key.key_hint,
        "created_at": key.created_at.isoformat(),
    }


@router.delete("/provider-keys/{key_id}")
async def delete_provider_key(
    key_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)
    result = await db.execute(
        select(LLMProviderKey).where(LLMProviderKey.id == _parse_uuid(key_id, "key_id"))
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    route_config = await _get_route_config(db)

    def _uses_key(target: dict[str, Any]) -> bool:
        if str(target.get("key_id") or "") == str(key.id):
            return True
        return any(
            str(child.get("key_id") or "") == str(key.id)
            for field in ("alternates", "fallbacks")
            for child in target.get(field, []) or []
            if isinstance(child, dict)
        )

    in_use_by = [
        route_name
        for route_name in ("standard", "premium")
        if _uses_key(route_config.get(route_name, {}) or {})
    ]
    if in_use_by:
        raise HTTPException(
            status_code=409,
            detail=(
                "Provider key is used by the active "
                + ", ".join(in_use_by)
                + " route. Activate a replacement route before deleting it."
            ),
        )
    await record_operator_audit(
        db,
        request,
        action="llm.provider_disabled",
        resource_type="llm_provider_key",
        resource_id=str(key.id),
        metadata={
            "provider_id": key.provider_id,
            "key_name": key.name,
            "key_hint": key.key_hint,
        },
    )
    await db.delete(key)
    await db.commit()
    return {"deleted": key_id}


@router.post("/provider-keys/sync-env")
async def sync_env_keys(request: Request, db: AsyncSession = Depends(get_db)):
    """Import DEEPSEEK_API_KEY and OPENROUTER_API_KEY from environment into the vault."""
    _require_platform_key(request)

    synced = []
    errors = []

    # Remove the old DEEPSEEK_API_KEY entry that was imported under the deprecated
    # provider_id "opencode-zen" before the remap to "opencode-go".
    old_deepseek = await db.execute(
        select(LLMProviderKey).where(
            LLMProviderKey.provider_id == "opencode-zen",
            LLMProviderKey.name == "OpenCode API Key (from env)",
        )
    )
    for stale in old_deepseek.scalars().all():
        await db.delete(stale)
        logger.info("sync_env_keys: removed stale opencode-zen env key %s", stale.id)

    env_map = [
        ("DEEPSEEK_API_KEY", "opencode-go", "OpenCode Go API Key (from env)"),
        ("OPENCODE_API_KEY", "opencode-zen", "OpenCode Zen API Key (from env)"),
        ("OPENROUTER_API_KEY", "openrouter", "OpenRouter API Key (from env)"),
    ]

    for env_var, provider_id, display_name in env_map:
        raw_key = getattr(settings, env_var, None) or ""
        if not raw_key.strip():
            errors.append(f"{env_var} is empty or not set")
            continue

        # Check if a key with this name already exists
        existing = await db.execute(
            select(LLMProviderKey).where(
                LLMProviderKey.provider_id == provider_id,
                LLMProviderKey.name == display_name,
            )
        )
        if existing.scalar_one_or_none():
            errors.append(f"{env_var}: key '{display_name}' already imported")
            continue

        hint = raw_key.strip()[-4:] if len(raw_key.strip()) >= 4 else "****"
        try:
            encrypted = encrypt_token(raw_key.strip())
        except Exception:
            errors.append(f"{env_var}: encryption failed")
            continue

        key = LLMProviderKey(
            name=display_name,
            provider_id=provider_id,
            encrypted_key=encrypted,
            key_hint=hint,
        )
        db.add(key)
        synced.append(
            {"env_var": env_var, "provider_id": provider_id, "name": display_name}
        )

    await db.commit()
    return {"synced": synced, "errors": errors}


@router.post("/provider-keys/{key_id}/fetch-models")
async def fetch_provider_models(
    key_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)
    result = await db.execute(
        select(LLMProviderKey).where(LLMProviderKey.id == _parse_uuid(key_id, "key_id"))
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    preset = _PRESET_BY_ID.get(key.provider_id)
    if not preset or not preset.get("models_url"):
        return {
            "models": (preset or {}).get("default_models", []),
            "provider_id": key.provider_id,
            "source": "preset",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        plaintext = decrypt_token(key.encrypted_key)
    except Exception:
        raise HTTPException(status_code=500, detail="Key decryption failed")

    try:
        models = await _fetch_models_from_provider(
            preset["base_url"], preset["models_url"], plaintext, key.provider_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Provider model fetch failed: {exc}"
        )

    for model in models:
        model["provider_id"] = key.provider_id
        model["key_id"] = str(key.id)
        model["key_ids"] = [str(key.id)]
        model["key_name"] = key.name
        model["key_names"] = [key.name]
    return {
        "models": models,
        "provider_id": key.provider_id,
        "source": "provider",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "free_count": sum(1 for model in models if model.get("is_free")),
    }


@router.get("/model-catalog")
async def get_model_catalog(request: Request, db: AsyncSession = Depends(get_db)):
    _require_platform_key(request)
    return await _get_model_catalog(db)


@router.post("/model-catalog/refresh")
async def refresh_model_catalog(request: Request, db: AsyncSession = Depends(get_db)):
    _require_platform_key(request)
    previous = await _get_model_catalog(db)
    previous_by_model = {
        (item.get("provider_id"), item.get("id")): item
        for item in previous.get("models", [])
        if isinstance(item, dict)
    }

    keys_result = await db.execute(
        select(LLMProviderKey).order_by(LLMProviderKey.provider_id, LLMProviderKey.name)
    )
    keys = keys_result.scalars().all()
    refreshed_at = datetime.now(timezone.utc)
    new_cutoff = refreshed_at - timedelta(days=7)
    models: list[dict] = []
    errors: list[dict] = []

    async def _fetch_one_key(key: LLMProviderKey) -> tuple[list[dict], str] | None:
        preset = _PRESET_BY_ID.get(key.provider_id)
        if not preset:
            raise RuntimeError(f"Unknown provider {key.provider_id}")
        if not preset.get("models_url"):
            fetched = [
                _normalize_model_item(item, key.provider_id)
                for item in preset.get("default_models", [])
            ]
            return [item for item in fetched if item], "preset"
        plaintext = decrypt_token(key.encrypted_key)
        fetched = await _fetch_models_from_provider(
            preset["base_url"],
            preset["models_url"],
            plaintext,
            key.provider_id,
        )
        return fetched, "provider"

    results = await asyncio.gather(
        *[_fetch_one_key(k) for k in keys], return_exceptions=True
    )

    for key, result in zip(keys, results):
        preset = _PRESET_BY_ID.get(key.provider_id, {})
        if isinstance(result, BaseException):
            logger.warning(
                "Model catalog refresh failed for key %s: %s", key.id, result
            )
            errors.append(
                {
                    "key_id": str(key.id),
                    "key_name": key.name,
                    "provider_id": key.provider_id,
                    "error": str(result)[:300],
                }
            )
            continue
        fetched, source = result
        for item in fetched:
            ident = (key.provider_id, item["id"])
            previous_item = previous_by_model.get(ident) or {}
            first_seen = previous_item.get("first_seen_at") or refreshed_at.isoformat()
            try:
                first_seen_dt = datetime.fromisoformat(first_seen)
            except ValueError:
                first_seen_dt = refreshed_at
            item.update(
                {
                    "key_id": str(key.id),
                    "key_ids": [str(key.id)],
                    "key_name": key.name,
                    "key_names": [key.name],
                    "provider_name": preset.get("name", key.provider_id),
                    "source": source,
                    "first_seen_at": first_seen,
                    "last_seen_at": refreshed_at.isoformat(),
                    "is_new": ident not in previous_by_model
                    or first_seen_dt >= new_cutoff,
                }
            )
            models.append(item)

    models = sorted(
        _merge_catalog_models(models),
        key=lambda item: (
            not item.get("is_new"),
            not item.get("is_free"),
            item.get("provider_name", ""),
            item.get("id", ""),
        ),
    )
    catalog = {
        "models": models,
        "last_refreshed_at": refreshed_at.isoformat(),
        "provider_count": len({model["provider_id"] for model in models}),
        "key_count": len({model["key_id"] for model in models}),
        "model_count": len(models),
        "free_count": sum(1 for model in models if model.get("is_free")),
        "free_legal_count": sum(
            1
            for model in models
            if model.get("is_free") and model.get("legal_eligible")
        ),
        "recommended_count": sum(
            1 for model in models if model.get("legal_tier") == "recommended"
        ),
        "excluded_count": sum(
            1 for model in models if model.get("legal_tier") == "excluded"
        ),
        "new_count": sum(1 for model in models if model.get("is_new")),
        "errors": errors,
    }
    await _save_model_catalog(db, catalog)
    await db.commit()
    return catalog


@router.post("/routes/recommend")
async def recommend_routes(
    request: Request,
    body: RouteRecommendationRequest,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)
    catalog = await _get_model_catalog(db)
    key_result = await db.execute(select(LLMProviderKey))
    keys = [
        {"id": str(key.id), "name": key.name, "provider_id": key.provider_id}
        for key in key_result.scalars().all()
    ]

    audit_result = await db.execute(
        select(OperatorAuditLog)
        .where(OperatorAuditLog.action == "llm.model_tested")
        .order_by(OperatorAuditLog.created_at.desc())
        .limit(250)
    )
    health: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in audit_result.scalars().all():
        metadata = entry.metadata_json or {}
        ident = (
            str(metadata.get("provider_id") or ""),
            str(metadata.get("model") or ""),
            str(metadata.get("key_id") or ""),
        )
        if not all(ident) or ident in health:
            continue
        health[ident] = {
            "ok": bool(metadata.get("ok")),
            "error_category": metadata.get("error_category"),
            "credential_state": metadata.get("credential_state"),
            "provider_latency_ms": metadata.get("provider_latency_ms"),
            "tested_at": entry.created_at,
        }

    recommendation = _recommend_route_targets(
        catalog=catalog,
        keys=keys,
        health=health,
        criteria=body,
    )
    await record_operator_audit(
        db,
        request,
        action="llm.routes_recommended",
        resource_type="llm_route_recommendation",
        resource_id=body.route,
        metadata={
            "criteria": body.model_dump(),
            "candidate_count": len(recommendation["candidates"]),
            "eligible_count": recommendation["eligible_count"],
            "providers": [
                candidate["provider_id"] for candidate in recommendation["candidates"]
            ],
            "models": [
                candidate["model"] for candidate in recommendation["candidates"]
            ],
        },
    )
    await db.commit()
    return recommendation


@router.get("/profiles")
async def list_routing_profiles(request: Request, db: AsyncSession = Depends(get_db)):
    _require_platform_key(request)
    profiles = list(
        (
            await db.scalars(select(LLMRoutingProfile).order_by(LLMRoutingProfile.name))
        ).all()
    )
    return {"profiles": [_profile_payload(profile) for profile in profiles]}


@router.post("/profiles", status_code=201)
async def create_routing_profile(
    body: RoutingProfileCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_platform_key(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Profile name is required")
    if await db.scalar(
        select(LLMRoutingProfile.id).where(LLMRoutingProfile.name == name)
    ):
        raise HTTPException(
            status_code=409, detail="Routing profile name already exists"
        )
    source = (
        await _selected_profile(db, body.clone_profile_id)
        if body.clone_profile_id
        else None
    )
    profile = LLMRoutingProfile(
        name=name,
        description=(body.description or "").strip() or None,
        standard_route=dict(source.standard_route or {}) if source else {},
        premium_route=dict(source.premium_route or {}) if source else {},
        standard_allow_matter_context=bool(source.standard_allow_matter_context)
        if source
        else False,
        premium_allow_matter_context=bool(source.premium_allow_matter_context)
        if source
        else True,
        is_default=False,
        is_active=True,
    )
    db.add(profile)
    await db.flush()
    await record_operator_audit(
        db,
        request,
        action="llm.routing_profile_created",
        resource_type="llm_routing_profile",
        resource_id=str(profile.id),
        metadata={
            "name": profile.name,
            "cloned_from": str(source.id) if source else None,
            "standard_allow_matter_context": profile.standard_allow_matter_context,
            "premium_allow_matter_context": profile.premium_allow_matter_context,
        },
    )
    await db.commit()
    await db.refresh(profile)
    return _profile_payload(profile)


@router.patch("/profiles/{profile_id}")
async def update_routing_profile(
    profile_id: str,
    body: RoutingProfileUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)
    profile = await _selected_profile(db, profile_id)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Profile name is required")
        duplicate = await db.scalar(
            select(LLMRoutingProfile.id).where(
                LLMRoutingProfile.name == name, LLMRoutingProfile.id != profile.id
            )
        )
        if duplicate:
            raise HTTPException(
                status_code=409, detail="Routing profile name already exists"
            )
        profile.name = name
    if body.description is not None:
        profile.description = body.description.strip() or None
    if body.is_active is not None:
        if not body.is_active and profile.is_default and not body.is_default:
            raise HTTPException(
                status_code=400, detail="The default routing profile cannot be inactive"
            )
        profile.is_active = body.is_active
    if body.is_default:
        for existing in (
            await db.scalars(
                select(LLMRoutingProfile).where(LLMRoutingProfile.is_default.is_(True))
            )
        ).all():
            existing.is_default = False
        await db.flush()
        profile.is_default = True
        profile.is_active = True
        aliases = (
            (profile.activation or {}).get("aliases", {})
            if isinstance(profile.activation, dict)
            else {}
        )
        if aliases.get("standard") and aliases.get("premium"):
            await upsert_platform_llm_config(
                db,
                {
                    "standard_provider": LITELLM_PROVIDER,
                    "standard_model": aliases["standard"],
                    "premium_provider": LITELLM_PROVIDER,
                    "premium_model": aliases["premium"],
                },
            )
    await record_operator_audit(
        db,
        request,
        action="llm.routing_profile_updated",
        resource_type="llm_routing_profile",
        resource_id=str(profile.id),
        metadata={
            "name": profile.name,
            "is_default": profile.is_default,
            "is_active": profile.is_active,
            "standard_allow_matter_context": profile.standard_allow_matter_context,
            "premium_allow_matter_context": profile.premium_allow_matter_context,
        },
    )
    await db.commit()
    return _profile_payload(profile)


@router.get("/routes")
async def get_routes(
    request: Request, profile_id: str | None = None, db: AsyncSession = Depends(get_db)
):
    _require_platform_key(request)
    profile = await _selected_profile(db, profile_id)
    config = (
        {
            "standard": {
                **dict(profile.standard_route or {}),
                "allow_matter_context": profile.standard_allow_matter_context,
            },
            "premium": {
                **dict(profile.premium_route or {}),
                "allow_matter_context": profile.premium_allow_matter_context,
            },
            "activation": profile.activation or {},
        }
        if profile
        else await _get_route_config(db)
    )

    # Hydrate with key hints (safe, no plaintext)
    keys_result = await db.execute(select(LLMProviderKey))
    keys_by_id = {str(k.id): k for k in keys_result.scalars().all()}

    def _hydrate(route_cfg: dict) -> dict:
        out = dict(route_cfg)
        kid = out.get("key_id")
        if kid and kid in keys_by_id:
            k = keys_by_id[kid]
            out["key_name"] = k.name
            out["key_hint"] = k.key_hint
            out["provider_name"] = _PRESET_BY_ID.get(k.provider_id, {}).get(
                "name", k.provider_id
            )
        for alternate in out.get("alternates", []):
            akid = alternate.get("key_id")
            if akid and akid in keys_by_id:
                ak = keys_by_id[akid]
                alternate["key_name"] = ak.name
                alternate["key_hint"] = ak.key_hint
                alternate["provider_name"] = _PRESET_BY_ID.get(ak.provider_id, {}).get(
                    "name", ak.provider_id
                )
        for fb in out.get("fallbacks", []):
            fkid = fb.get("key_id")
            if fkid and fkid in keys_by_id:
                fk = keys_by_id[fkid]
                fb["key_name"] = fk.name
                fb["key_hint"] = fk.key_hint
                fb["provider_name"] = _PRESET_BY_ID.get(fk.provider_id, {}).get(
                    "name", fk.provider_id
                )
        return out

    return {
        "standard": _hydrate(config.get("standard", {})),
        "premium": _hydrate(config.get("premium", {})),
        "activation": config.get("activation", {}),
        "providers": PROVIDER_PRESETS,
        "profile": _profile_payload(profile) if profile else None,
    }


@router.put("/routes")
async def save_routes(
    request: Request,
    body: RoutesUpdate,
    profile_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)

    keys_result = await db.execute(select(LLMProviderKey))
    keys_by_id = {str(k.id): k for k in keys_result.scalars().all()}

    def _validate_route_entry(entry: RouteEntry, label: str) -> None:
        fields = {
            "provider_id": _clean_optional(entry.provider_id),
            "key_id": _clean_optional(entry.key_id),
            "model": _clean_optional(entry.model),
        }
        populated = [name for name, value in fields.items() if value]
        if not populated:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: an active primary provider, key, and model are required.",
            )
        if populated and len(populated) != len(fields):
            missing = ", ".join(name for name, value in fields.items() if not value)
            raise HTTPException(
                status_code=400,
                detail=f"{label}: complete provider, key, and model or clear the route. Missing {missing}.",
            )
        if fields["provider_id"] and fields["provider_id"] not in _PRESET_BY_ID:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: unknown provider_id {fields['provider_id']!r}",
            )
        if fields["key_id"] and fields["key_id"] not in keys_by_id:
            raise HTTPException(
                status_code=400,
                detail=f"{label}: key_id {fields['key_id']!r} not found",
            )
        if fields["key_id"]:
            key = keys_by_id[fields["key_id"]]
            if key.provider_id != fields["provider_id"]:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{label}: selected key belongs to {key.provider_id}, "
                        f"not {fields['provider_id']}"
                    ),
                )

        def _validate_targets(targets: list[dict[str, Any]], kind: str) -> None:
            for i, target in enumerate(targets):
                target_fields = {
                    "provider_id": _clean_optional(target.get("provider_id")),
                    "key_id": _clean_optional(target.get("key_id")),
                    "model": _clean_optional(target.get("model")),
                }
                target_populated = [
                    name for name, value in target_fields.items() if value
                ]
                if target_populated and len(target_populated) != len(target_fields):
                    missing = ", ".join(
                        name for name, value in target_fields.items() if not value
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"{label} {kind}[{i}]: missing {missing}",
                    )
                if not target_populated:
                    continue
                if target_fields["provider_id"] not in _PRESET_BY_ID:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{label} {kind}[{i}]: unknown provider_id "
                            f"{target_fields['provider_id']!r}"
                        ),
                    )
                target_key_id = target_fields["key_id"]
                if target_key_id not in keys_by_id:
                    logger.warning(
                        "%s %s[%d]: key_id %r not found — pruning stale reference",
                        label,
                        kind,
                        i,
                        target_key_id,
                    )
                    continue  # drop stale target rather than rejecting the save
                if (
                    keys_by_id[target_key_id].provider_id
                    != target_fields["provider_id"]
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{label} {kind}[{i}]: selected key belongs to "
                            f"{keys_by_id[target_key_id].provider_id}, "
                            f"not {target_fields['provider_id']}"
                        ),
                    )

        _validate_targets(entry.alternates, "alternate")
        _validate_targets(entry.fallbacks, "fallback")

    _validate_route_entry(body.standard, "standard")
    _validate_route_entry(body.premium, "premium")

    def _normalize_route_entry(entry: RouteEntry) -> dict:
        route = {
            "key_id": _clean_optional(entry.key_id) or None,
            "provider_id": _clean_optional(entry.provider_id) or None,
            "model": _clean_optional(entry.model) or None,
            "capacity": _capacity(entry.capacity),
            "alternates": [],
            "fallbacks": [],
            "allow_matter_context": bool(entry.allow_matter_context),
        }
        for alternate in entry.alternates:
            normalized = {
                "key_id": _clean_optional(alternate.get("key_id")) or None,
                "provider_id": _clean_optional(alternate.get("provider_id")) or None,
                "model": _clean_optional(alternate.get("model")) or None,
                "capacity": _capacity(alternate.get("capacity")),
            }
            if not any(
                normalized.get(field) for field in ("key_id", "provider_id", "model")
            ):
                continue
            kid = normalized.get("key_id")
            if kid and kid not in keys_by_id:
                logger.warning(
                    "_normalize_route_entry: pruning stale alternate key_id %r", kid
                )
                continue
            route["alternates"].append(normalized)
        for fallback in entry.fallbacks:
            normalized = {
                "key_id": _clean_optional(fallback.get("key_id")) or None,
                "provider_id": _clean_optional(fallback.get("provider_id")) or None,
                "model": _clean_optional(fallback.get("model")) or None,
                "capacity": _capacity(fallback.get("capacity")),
            }
            if not any(
                normalized.get(field) for field in ("key_id", "provider_id", "model")
            ):
                continue
            kid = normalized.get("key_id")
            if kid and kid not in keys_by_id:
                logger.warning(
                    "_normalize_route_entry: pruning stale fallback key_id %r", kid
                )
                continue
            route["fallbacks"].append(normalized)
        return route

    profile = await _selected_profile(db, profile_id)
    config = {
        "standard": _normalize_route_entry(body.standard),
        "premium": _normalize_route_entry(body.premium),
    }
    await _enforce_customer_route_data_policy(request, db, config)
    aliases = _managed_route_aliases(config)
    reload_result = await _reload_litellm_routes(
        config,
        keys_by_id,
        aliases=aliases,
        validate=True,
    )
    activated = bool(reload_result.get("litellm_updated"))
    if activated:
        config["activation"] = {
            "status": "active",
            "revision": _route_revision(config),
            "aliases": aliases,
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }
        if profile:
            profile.standard_route = {
                k: v
                for k, v in config["standard"].items()
                if k != "allow_matter_context"
            }
            profile.premium_route = {
                k: v
                for k, v in config["premium"].items()
                if k != "allow_matter_context"
            }
            profile.standard_allow_matter_context = body.standard.allow_matter_context
            profile.premium_allow_matter_context = body.premium.allow_matter_context
            profile.activation = config["activation"]
        else:
            await _save_route_config(db, config)
        if profile is None or profile.is_default:
            await upsert_platform_llm_config(
                db,
                {
                    "standard_provider": LITELLM_PROVIDER,
                    "standard_model": aliases["standard"],
                    "premium_provider": LITELLM_PROVIDER,
                    "premium_model": aliases["premium"],
                },
            )
    audit_metadata = _route_audit_payload(config, reload_result)
    if profile:
        audit_metadata.update(
            {"profile_id": str(profile.id), "profile_name": profile.name}
        )
    await record_operator_audit(
        db,
        request,
        action="llm.routes_saved" if activated else "llm.routes_activation_failed",
        resource_type="llm_routing_profile" if profile else "llm_route_config",
        resource_id=str(profile.id) if profile else LLM_ROUTE_CONFIG_KEY,
        metadata=audit_metadata,
    )
    await db.commit()
    return {
        "saved": activated,
        "activated": activated,
        **reload_result,
        "gateway_status": await _check_litellm_gateway(list(aliases.values())),
    }


@router.get("/gateway/status")
async def get_gateway_status(request: Request, db: AsyncSession = Depends(get_db)):
    _require_platform_key(request)
    config = await get_platform_llm_config(db)
    aliases = [
        str(config.get("standard_model") or "").strip(),
        str(config.get("premium_model") or "").strip(),
    ]
    return await _check_litellm_gateway([alias for alias in aliases if alias])


@router.post("/routes/reload")
async def reload_routes(
    request: Request,
    profile_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    _require_platform_key(request)
    profile = await _selected_profile(db, profile_id)
    config = (
        {
            "standard": {
                **dict(profile.standard_route or {}),
                "allow_matter_context": profile.standard_allow_matter_context,
            },
            "premium": {
                **dict(profile.premium_route or {}),
                "allow_matter_context": profile.premium_allow_matter_context,
            },
            "activation": profile.activation or {},
        }
        if profile
        else await _get_route_config(db)
    )
    await _enforce_customer_route_data_policy(request, db, config)
    keys_result = await db.execute(select(LLMProviderKey))
    keys_by_id = {str(k.id): k for k in keys_result.scalars().all()}
    activation = config.get("activation", {}) or {}
    aliases = activation.get("aliases") or _managed_route_aliases(config)
    reload_result = await _reload_litellm_routes(
        config, keys_by_id, aliases=aliases, validate=True
    )
    return {
        "reloaded": reload_result["litellm_updated"],
        "profile_id": str(profile.id) if profile else None,
        **reload_result,
        "gateway_status": await _check_litellm_gateway(list(aliases.values())),
    }


@router.post("/routes/test")
async def test_route(
    request: Request,
    body: RouteTestRequest,
    db: AsyncSession = Depends(get_db),
):
    server_start = time.monotonic()
    _require_platform_key(request)

    result = await db.execute(
        select(LLMProviderKey).where(
            LLMProviderKey.id == _parse_uuid(body.key_id, "key_id")
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    preset = _PRESET_BY_ID.get(body.provider_id)
    if not preset:
        raise HTTPException(
            status_code=400, detail=f"Unknown provider: {body.provider_id}"
        )
    if key.provider_id != body.provider_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Selected key belongs to {key.provider_id}, not {body.provider_id}"
            ),
        )

    try:
        plaintext = decrypt_token(key.encrypted_key)
    except Exception:
        raise HTTPException(status_code=500, detail="Key decryption failed")

    def _timing_response(
        *,
        ok: bool,
        provider_latency_ms: int,
        model_used: str | None = None,
        response_preview: str | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        error_category: str | None = None,
        credential_state: str = "valid",
        http_status: int | None = None,
        provider_reachable: bool = False,
    ) -> dict[str, Any]:
        server_elapsed_ms = int((time.monotonic() - server_start) * 1000)
        payload: dict[str, Any] = {
            "ok": ok,
            "latency_ms": provider_latency_ms,
            "provider_latency_ms": provider_latency_ms,
            "server_elapsed_ms": server_elapsed_ms,
            "server_overhead_ms": max(0, server_elapsed_ms - provider_latency_ms),
            "capability": "text",
            "credential_state": credential_state,
            "provider_reachable": provider_reachable,
        }
        if model_used:
            payload["model_used"] = model_used
        if response_preview is not None:
            payload["response_preview"] = response_preview
        if usage:
            payload.update(usage)
        if error:
            payload["error"] = error
        if error_category:
            payload["error_category"] = error_category
        if http_status is not None:
            payload["http_status"] = http_status
        return payload

    # Test the selected provider directly; route saves separately register LiteLLM aliases.
    provider_start = time.monotonic()
    try:
        mode = preset.get("litellm_mode", "openai_compatible")
        api_mode = _provider_api_mode(body.provider_id, body.model)
        if mode == "anthropic" or api_mode == "messages":
            message_headers = {
                "x-api-key": plaintext,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            # OpenCode documents these models through the Anthropic-compatible
            # endpoint but uses its own bearer token. Sending both credential
            # forms keeps its documented SDK path and its gateway auth working.
            if mode != "anthropic":
                message_headers["Authorization"] = f"Bearer {plaintext}"
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    (
                        f"{preset['base_url']}/v1/messages"
                        if mode == "anthropic"
                        else f"{preset['base_url']}/messages"
                    ),
                    headers=message_headers,
                    json={
                        "model": body.model,
                        "max_tokens": PROVIDER_CANARY_MAX_TOKENS,
                        "temperature": 0,
                        "system": "Follow the user's output format exactly.",
                        "messages": [
                            {"role": "user", "content": "Reply with exactly: OK"}
                        ],
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
            elapsed_ms = int((time.monotonic() - provider_start) * 1000)
            parts = payload.get("content") or []
            text = " ".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
            usage = payload.get("usage") or {}
            canary_ok = canary_answer_matches(text)
            drained = _canary_reasoning_drain(payload, text)
            result = _timing_response(
                ok=canary_ok,
                provider_latency_ms=elapsed_ms,
                model_used=payload.get("model") or body.model,
                response_preview=text[:120],
                usage=_usage_payload(
                    prompt_tokens=usage.get("input_tokens"),
                    completion_tokens=usage.get("output_tokens"),
                    elapsed_ms=elapsed_ms,
                ),
                error=_canary_error_message(canary_ok, drained),
                error_category=_canary_error_category(canary_ok, drained),
                provider_reachable=True,
            )
            await record_operator_audit(
                db,
                request,
                action="llm.model_tested",
                resource_type="llm_provider_key",
                resource_id=str(key.id),
                metadata=_model_test_audit_payload(body=body, key=key, result=result),
            )
            await db.commit()
            return result

        if api_mode == "responses":
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{preset['base_url']}/responses",
                    headers={
                        "Authorization": f"Bearer {plaintext}",
                        "content-type": "application/json",
                    },
                    json={
                        "model": body.model,
                        "input": [
                            {
                                "role": "developer",
                                "content": "Follow the user's output format exactly.",
                            },
                            {"role": "user", "content": "Reply with exactly: OK"},
                        ],
                        "max_output_tokens": PROVIDER_CANARY_MAX_TOKENS,
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
            elapsed_ms = int((time.monotonic() - provider_start) * 1000)
            text = _responses_output_text(payload)
            usage = payload.get("usage") or {}
            canary_ok = canary_answer_matches(text)
            drained = _canary_reasoning_drain(payload, text)
            result = _timing_response(
                ok=canary_ok,
                provider_latency_ms=elapsed_ms,
                model_used=payload.get("model") or body.model,
                response_preview=text[:120],
                usage=_usage_payload(
                    prompt_tokens=usage.get("input_tokens"),
                    completion_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    elapsed_ms=elapsed_ms,
                ),
                error=_canary_error_message(canary_ok, drained),
                error_category=_canary_error_category(canary_ok, drained),
                provider_reachable=True,
            )
            await record_operator_audit(
                db,
                request,
                action="llm.model_tested",
                resource_type="llm_provider_key",
                resource_id=str(key.id),
                metadata=_model_test_audit_payload(body=body, key=key, result=result),
            )
            await db.commit()
            return result

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=plaintext,
            base_url=preset["base_url"],
            timeout=20.0,
            max_retries=0,
        )
        resp = await client.chat.completions.create(
            model=body.model,
            messages=[
                {
                    "role": "system",
                    "content": "Follow the user's output format exactly.",
                },
                {"role": "user", "content": "Reply with exactly: OK"},
            ],
            max_tokens=PROVIDER_CANARY_MAX_TOKENS,
            temperature=0,
            extra_body=(
                {"provider": dict(OPENROUTER_CONFIDENTIAL_PROVIDER_PREFERENCES)}
                if body.provider_id == "openrouter"
                else None
            ),
        )
        elapsed_ms = int((time.monotonic() - provider_start) * 1000)
        text = (resp.choices[0].message.content or "").strip()
        model_used = resp.model or body.model
        usage = resp.usage
        canary_ok = canary_answer_matches(text)
        drained = _canary_reasoning_drain(
            resp.model_dump() if hasattr(resp, "model_dump") else {}, text
        )
        result = _timing_response(
            ok=canary_ok,
            provider_latency_ms=elapsed_ms,
            model_used=model_used,
            response_preview=text[:120],
            usage=_usage_payload(
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
                elapsed_ms=elapsed_ms,
            ),
            error=_canary_error_message(canary_ok, drained),
            error_category=_canary_error_category(canary_ok, drained),
            provider_reachable=True,
        )
        await record_operator_audit(
            db,
            request,
            action="llm.model_tested",
            resource_type="llm_provider_key",
            resource_id=str(key.id),
            metadata=_model_test_audit_payload(body=body, key=key, result=result),
        )
        await db.commit()
        return result
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - provider_start) * 1000)
        evidence = _provider_error_evidence(exc)
        result = _timing_response(
            ok=False,
            provider_latency_ms=elapsed_ms,
            **evidence,
        )
        await record_operator_audit(
            db,
            request,
            action="llm.model_tested",
            resource_type="llm_provider_key",
            resource_id=str(key.id),
            metadata=_model_test_audit_payload(body=body, key=key, result=result),
        )
        await db.commit()
        return result
