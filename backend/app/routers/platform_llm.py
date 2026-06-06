"""
Platform LLM Provider Route Builder.

Authenticated by X-Platform-Key header (same as platform.py).

Endpoints:
  GET  /api/platform/llm/providers                    — list provider presets
  GET  /api/platform/llm/provider-keys                — list stored keys (masked)
  POST /api/platform/llm/provider-keys                — add encrypted key
  DELETE /api/platform/llm/provider-keys/{id}         — delete key
  POST /api/platform/llm/provider-keys/sync-env       — import env vars into vault
  POST /api/platform/llm/provider-keys/{id}/fetch-models — list models from provider
  GET  /api/platform/llm/routes                       — current route config
  PUT  /api/platform/llm/routes                       — save routes (hot-reloads LiteLLM)
  POST /api/platform/llm/routes/test                  — test a route with synthetic prompt
"""

import hmac
import logging
import uuid
from datetime import datetime, timezone
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
from app.services.llm_routing import LITELLM_PROVIDER, upsert_platform_llm_config
from app.services.token_vault import decrypt_token, encrypt_token

settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform/llm", tags=["platform-llm"])

LLM_ROUTE_CONFIG_KEY = "llm_route_config_v2"

# ── Auth ────────────────────────────────────────────────────────────────────


def _require_platform_key(request: Request) -> None:
    key = request.headers.get("X-Platform-Key", "")
    secret = settings.PLATFORM_SECRET_KEY
    if not secret or len(secret) < 32 or not hmac.compare_digest(key, secret):
        raise HTTPException(status_code=403, detail="Invalid or missing platform key")


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
    alias: str, provider_id: str, model: str, plaintext_key: str
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
    return entry


async def _call_litellm_config_update(
    new_model_list: list[dict], fallbacks: list[dict]
) -> bool:
    """Hot-reload LiteLLM router via /config/update with the updated model list."""
    if not settings.LITELLM_BASE_URL or not settings.LITELLM_API_KEY:
        return False
    payload: dict[str, Any] = {"model_list": new_model_list}
    if fallbacks:
        payload["router_settings"] = {"fallbacks": fallbacks}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{settings.LITELLM_BASE_URL}/config/update",
                headers={"Authorization": f"Bearer {settings.LITELLM_API_KEY}"},
                json=payload,
            )
            if resp.status_code in (200, 204):
                return True
            logger.warning(
                "LiteLLM /config/update returned %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
    except Exception as exc:
        logger.warning("LiteLLM config update failed: %s", exc)
        return False


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
                if isinstance(item, dict):
                    mid = (
                        item.get("id") or item.get("model_id") or item.get("name") or ""
                    )
                    if mid:
                        models.append({"id": mid, "name": item.get("name", mid)})
                elif isinstance(item, str):
                    models.append({"id": item, "name": item})
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
    await db.delete(key)
    await db.commit()
    return {"deleted": key_id}


@router.post("/provider-keys/sync-env")
async def sync_env_keys(request: Request, db: AsyncSession = Depends(get_db)):
    """Import DEEPSEEK_API_KEY and OPENROUTER_API_KEY from environment into the vault."""
    _require_platform_key(request)

    synced = []
    errors = []

    env_map = [
        ("DEEPSEEK_API_KEY", "opencode-zen", "OpenCode API Key (from env)"),
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

    return {"models": models, "provider_id": key.provider_id, "source": "provider"}


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
        for i, fb in enumerate(entry.fallbacks):
            fb_fields = {
                "provider_id": _clean_optional(fb.get("provider_id")),
                "key_id": _clean_optional(fb.get("key_id")),
                "model": _clean_optional(fb.get("model")),
            }
            fb_populated = [name for name, value in fb_fields.items() if value]
            if fb_populated and len(fb_populated) != len(fb_fields):
                missing = ", ".join(
                    name for name, value in fb_fields.items() if not value
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"{label} fallback[{i}]: missing {missing}",
                )
            if not fb_populated:
                continue
            if fb_fields["provider_id"] not in _PRESET_BY_ID:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{label} fallback[{i}]: unknown provider_id "
                        f"{fb_fields['provider_id']!r}"
                    ),
                )
            fkid = fb_fields["key_id"]
            if fkid not in keys_by_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"{label} fallback[{i}]: key_id {fkid!r} not found",
                )
            if keys_by_id[fkid].provider_id != fb_fields["provider_id"]:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{label} fallback[{i}]: selected key belongs to "
                        f"{keys_by_id[fkid].provider_id}, not {fb_fields['provider_id']}"
                    ),
                )

    _validate_route_entry(body.standard, "standard")
    _validate_route_entry(body.premium, "premium")

    def _normalize_route_entry(entry: RouteEntry) -> dict:
        route = {
            "key_id": _clean_optional(entry.key_id) or None,
            "provider_id": _clean_optional(entry.provider_id) or None,
            "model": _clean_optional(entry.model) or None,
            "fallbacks": [],
        }
        for fallback in entry.fallbacks:
            normalized = {
                "key_id": _clean_optional(fallback.get("key_id")) or None,
                "provider_id": _clean_optional(fallback.get("provider_id")) or None,
                "model": _clean_optional(fallback.get("model")) or None,
            }
            if any(normalized.values()):
                route["fallbacks"].append(normalized)
        return route

    config = {
        "standard": _normalize_route_entry(body.standard),
        "premium": _normalize_route_entry(body.premium),
    }
    await _save_route_config(db, config)
    await upsert_platform_llm_config(
        db,
        {
            "standard_provider": LITELLM_PROVIDER,
            "standard_model": settings.LITELLM_STANDARD_MODEL,
            "premium_provider": LITELLM_PROVIDER,
            "premium_model": settings.LITELLM_PREMIUM_MODEL,
        },
    )

    # Build new LiteLLM model list and hot-reload
    new_models: list[dict] = []
    fallback_settings: list[dict] = []
    litellm_updated = False

    def _add_model(alias: str, route_dict: dict) -> None:
        kid = route_dict.get("key_id")
        model = route_dict.get("model", "")
        pid = route_dict.get("provider_id", "")
        if not kid or not model or not pid:
            return
        k = keys_by_id.get(kid)
        if not k:
            return
        try:
            plaintext = decrypt_token(k.encrypted_key)
        except Exception:
            logger.warning("Failed to decrypt key %s for LiteLLM update", kid)
            return
        new_models.append(_build_litellm_model_entry(alias, pid, model, plaintext))

    standard_alias, standard_fallbacks = _route_aliases("standard", config["standard"])
    premium_alias, premium_fallbacks = _route_aliases("premium", config["premium"])

    _add_model(standard_alias, config["standard"])
    for i, fb in enumerate(config["standard"].get("fallbacks", [])):
        _add_model(f"{standard_alias}-fb-{i}", fb)

    _add_model(premium_alias, config["premium"])
    for i, fb in enumerate(config["premium"].get("fallbacks", [])):
        _add_model(f"{premium_alias}-fb-{i}", fb)

    if standard_fallbacks:
        fallback_settings.append({standard_alias: standard_fallbacks})
    if premium_fallbacks:
        fallback_settings.append({premium_alias: premium_fallbacks})

    if new_models:
        litellm_updated = await _call_litellm_config_update(
            new_models, fallback_settings
        )

    await db.commit()
    return {
        "saved": True,
        "litellm_updated": litellm_updated,
        "models_registered": len(new_models),
        "fallbacks_registered": sum(len(item[next(iter(item))]) for item in fallback_settings),
        "app_aliases": {
            "standard": settings.LITELLM_STANDARD_MODEL,
            "premium": settings.LITELLM_PREMIUM_MODEL,
        },
    }


@router.post("/routes/test")
async def test_route(
    request: Request,
    body: RouteTestRequest,
    db: AsyncSession = Depends(get_db),
):
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

    # Test the selected provider directly; route saves separately register LiteLLM aliases.
    import time

    start = time.monotonic()
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
            elapsed_ms = int((time.monotonic() - start) * 1000)
            parts = payload.get("content") or []
            text = " ".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
            return {
                "ok": True,
                "latency_ms": elapsed_ms,
                "model_used": payload.get("model") or body.model,
                "response_preview": text[:120],
            }

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=plaintext, base_url=preset["base_url"])
        resp = await client.chat.completions.create(
            model=body.model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=10,
            temperature=0,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        text = (resp.choices[0].message.content or "").strip()
        model_used = resp.model or body.model
        return {
            "ok": True,
            "latency_ms": elapsed_ms,
            "model_used": model_used,
            "response_preview": text[:120],
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "ok": False,
            "latency_ms": elapsed_ms,
            "error": str(exc)[:300],
        }
