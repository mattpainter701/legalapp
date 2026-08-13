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
  PUT  /api/platform/llm/routes                       — save routes (hot-reloads LiteLLM)
  GET  /api/platform/llm/gateway/status               — LiteLLM reachability + alias status
  POST /api/platform/llm/routes/reload                — reload saved routes into LiteLLM
  POST /api/platform/llm/routes/test                  — test a route with synthetic prompt
"""

import asyncio
import hashlib
import json
import logging
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
from app.models.platform import PlatformSetting
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
FREE_CAPACITY_BLOCK_CODE = "free_capacity_not_allowed"

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


class RoutesUpdate(BaseModel):
    standard: RouteEntry
    premium: RouteEntry


class RouteTestRequest(BaseModel):
    key_id: str
    provider_id: str
    model: str
    route: str = "standard"


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


def _route_audit_payload(
    config: dict[str, Any], reload_result: dict[str, Any]
) -> dict[str, Any]:
    def _target_payload(target: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider_id": target.get("provider_id"),
            "key_id": target.get("key_id"),
            "model": target.get("model"),
            "capacity": target.get("capacity"),
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
        "ok": bool(result.get("ok")),
        "model_used": result.get("model_used"),
        "provider_latency_ms": result.get("provider_latency_ms"),
        "server_elapsed_ms": result.get("server_elapsed_ms"),
        "prompt_tokens": result.get("prompt_tokens"),
        "completion_tokens": result.get("completion_tokens"),
        "total_tokens": result.get("total_tokens"),
        "error": result.get("error"),
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


def _is_free_model(model_id: str, item: dict[str, Any]) -> bool:
    mid = (model_id or "").lower()
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


def _free_capacity_targets(
    config: dict[str, Any], catalog: dict[str, Any]
) -> list[dict[str, str]]:
    """Return customer-route targets backed by explicitly free capacity.

    Model IDs are checked even when the catalog is empty or stale. When a live
    catalog row is available, its normalized pricing metadata also participates
    so a zero-priced model without a conventional ``:free`` suffix is blocked.
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
        if _is_free_model(model_id, row):
            blocked.append(
                {
                    "route": route_name,
                    "placement": placement,
                    "provider_id": provider_id,
                    "model": model_id,
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


async def _enforce_customer_route_capacity_policy(
    request: Request,
    db: AsyncSession,
    config: dict[str, Any],
) -> None:
    """Reject customer aliases backed by free capacity and audit the attempt."""

    catalog = await _get_model_catalog(db)
    free_capacity_targets = _free_capacity_targets(config, catalog)
    if not free_capacity_targets:
        return
    await record_operator_audit(
        db,
        request,
        action="llm.routes_activation_blocked",
        resource_type="llm_route_config",
        resource_id=LLM_ROUTE_CONFIG_KEY,
        metadata={
            "reason": FREE_CAPACITY_BLOCK_CODE,
            "targets": free_capacity_targets,
        },
    )
    await db.commit()
    raise HTTPException(
        status_code=409,
        detail={
            "code": FREE_CAPACITY_BLOCK_CODE,
            "message": (
                "Standard and Premium customer routes require paid capacity. "
                "Use a separate lab-only alias for free models."
            ),
            "targets": free_capacity_targets,
        },
    )


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

    # 1. Architecture modality (OpenRouter)
    architecture = item.get("architecture") or {}
    if isinstance(architecture, dict):
        modality = (architecture.get("modality") or "").lower()
        input_modalities = {
            str(value).lower()
            for value in architecture.get("input_modalities") or []
        }
        output_modalities = {
            str(value).lower()
            for value in architecture.get("output_modalities") or []
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

    if not is_free:
        exclusion_reasons.append("not_free")

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
    is_free = _is_free_model(mid, item)
    latency_ms = _extract_latency_ms(item)
    eligibility = _legal_model_eligibility(
        item,
        model_id=mid,
        capabilities=capabilities,
        is_free=is_free,
        context_length=ctx,
        max_completion_tokens=max_completion_tokens,
        latency_ms=latency_ms,
    )
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
        **eligibility,
    }


def _hydrate_legal_eligibility(model: dict[str, Any]) -> dict[str, Any]:
    """Add eligibility fields to older saved catalog rows."""
    if "legal_eligible" in model and "legal_tier" in model:
        return model
    hydrated = dict(model)
    eligibility = _legal_model_eligibility(
        hydrated,
        model_id=str(hydrated.get("id") or ""),
        capabilities=hydrated.get("capabilities") or [],
        is_free=bool(hydrated.get("is_free")),
        context_length=hydrated.get("context_length"),
        max_completion_tokens=hydrated.get("max_completion_tokens"),
        latency_ms=_extract_latency_ms(hydrated),
    )
    hydrated.update(eligibility)
    return hydrated


def _hydrate_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(catalog or {})
    models = [
        _hydrate_legal_eligibility(model)
        for model in hydrated.get("models", [])
        if isinstance(model, dict)
    ]
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
    return hydrated


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
                            {"role": "user", "content": "Reply with exactly OK"}
                        ],
                        "temperature": 0,
                        "max_tokens": 8,
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
    base_url: str, models_url: str, plaintext_key: str
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
                model = _normalize_model_item(item, "")
                if model:
                    model["provider_id"] = ""
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
            preset["base_url"], preset["models_url"], plaintext
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Provider model fetch failed: {exc}"
        )

    for model in models:
        model["provider_id"] = key.provider_id
        model["key_id"] = str(key.id)
        model["key_name"] = key.name
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
    previous_by_key = {
        (
            item.get("provider_id"),
            item.get("key_id"),
            item.get("id"),
        ): item
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
            preset["base_url"], preset["models_url"], plaintext
        )
        for item in fetched:
            item["provider_id"] = key.provider_id
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
            ident = (key.provider_id, str(key.id), item["id"])
            previous_item = previous_by_key.get(ident) or {}
            first_seen = previous_item.get("first_seen_at") or refreshed_at.isoformat()
            try:
                first_seen_dt = datetime.fromisoformat(first_seen)
            except ValueError:
                first_seen_dt = refreshed_at
            item.update(
                {
                    "key_id": str(key.id),
                    "key_name": key.name,
                    "provider_name": preset.get("name", key.provider_id),
                    "source": source,
                    "first_seen_at": first_seen,
                    "last_seen_at": refreshed_at.isoformat(),
                    "is_new": ident not in previous_by_key
                    or first_seen_dt >= new_cutoff,
                }
            )
            models.append(item)

    models = sorted(
        models,
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


@router.get("/routes")
async def get_routes(request: Request, db: AsyncSession = Depends(get_db)):
    _require_platform_key(request)
    config = await _get_route_config(db)

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
    }


@router.put("/routes")
async def save_routes(
    request: Request,
    body: RoutesUpdate,
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

    config = {
        "standard": _normalize_route_entry(body.standard),
        "premium": _normalize_route_entry(body.premium),
    }
    await _enforce_customer_route_capacity_policy(request, db, config)
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
        await _save_route_config(db, config)
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
        action="llm.routes_saved" if activated else "llm.routes_activation_failed",
        resource_type="llm_route_config",
        resource_id=LLM_ROUTE_CONFIG_KEY,
        metadata=_route_audit_payload(config, reload_result),
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
async def reload_routes(request: Request, db: AsyncSession = Depends(get_db)):
    _require_platform_key(request)
    config = await _get_route_config(db)
    await _enforce_customer_route_capacity_policy(request, db, config)
    keys_result = await db.execute(select(LLMProviderKey))
    keys_by_id = {str(k.id): k for k in keys_result.scalars().all()}
    activation = config.get("activation", {}) or {}
    aliases = activation.get("aliases") or _managed_route_aliases(config)
    reload_result = await _reload_litellm_routes(
        config, keys_by_id, aliases=aliases, validate=True
    )
    return {
        "reloaded": reload_result["litellm_updated"],
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
    ) -> dict[str, Any]:
        server_elapsed_ms = int((time.monotonic() - server_start) * 1000)
        payload: dict[str, Any] = {
            "ok": ok,
            "latency_ms": provider_latency_ms,
            "provider_latency_ms": provider_latency_ms,
            "server_elapsed_ms": server_elapsed_ms,
            "server_overhead_ms": max(0, server_elapsed_ms - provider_latency_ms),
        }
        if model_used:
            payload["model_used"] = model_used
        if response_preview is not None:
            payload["response_preview"] = response_preview
        if usage:
            payload.update(usage)
        if error:
            payload["error"] = error
        return payload

    # Test the selected provider directly; route saves separately register LiteLLM aliases.
    provider_start = time.monotonic()
    try:
        mode = preset.get("litellm_mode", "openai_compatible")
        if mode == "anthropic":
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{preset['base_url']}/v1/messages",
                    headers={
                        "x-api-key": plaintext,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": body.model,
                        "max_tokens": 10,
                        "temperature": 0,
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
            result = _timing_response(
                ok=True,
                provider_latency_ms=elapsed_ms,
                model_used=payload.get("model") or body.model,
                response_preview=text[:120],
                usage=_usage_payload(
                    prompt_tokens=usage.get("input_tokens"),
                    completion_tokens=usage.get("output_tokens"),
                    elapsed_ms=elapsed_ms,
                ),
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

        client = AsyncOpenAI(api_key=plaintext, base_url=preset["base_url"])
        resp = await client.chat.completions.create(
            model=body.model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=10,
            temperature=0,
        )
        elapsed_ms = int((time.monotonic() - provider_start) * 1000)
        text = (resp.choices[0].message.content or "").strip()
        model_used = resp.model or body.model
        usage = resp.usage
        result = _timing_response(
            ok=True,
            provider_latency_ms=elapsed_ms,
            model_used=model_used,
            response_preview=text[:120],
            usage=_usage_payload(
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
                elapsed_ms=elapsed_ms,
            ),
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
        result = _timing_response(
            ok=False,
            provider_latency_ms=elapsed_ms,
            error=str(exc)[:300],
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
