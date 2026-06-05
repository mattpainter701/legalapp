from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.platform import PlatformSetting
from app.models.tenant import TenantSettings

settings = get_settings()

LLM_ROUTING_KEY = "llm_routing"
VALID_LLM_PROVIDERS = {
    "deepseek",
    "opencode",
    "openrouter",
    "litellm",
    "anthropic",
    "azure",
    "gemini",
}


@dataclass(frozen=True)
class LLMRoute:
    provider: str
    model: str


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def provider_default_model(provider: str, use_premium: bool = False) -> str:
    if provider == "litellm":
        return (
            settings.LITELLM_PREMIUM_MODEL
            if use_premium
            else settings.LITELLM_STANDARD_MODEL
        )
    if provider == "anthropic":
        return settings.PREMIUM_LLM
    if provider == "azure":
        return settings.AZURE_OPENAI_DEPLOYMENT or settings.PREMIUM_LLM
    if provider == "gemini":
        return "gemini-2.0-flash"
    if provider == "openrouter":
        configured = [
            m.strip() for m in settings.OPENROUTER_FREE_MODELS.split(",") if m.strip()
        ]
        if configured:
            return configured[0]
    return settings.PREMIUM_LLM if use_premium else settings.PRIMARY_LLM


def fallback_route(use_premium: bool) -> LLMRoute:
    if settings.LITELLM_ENABLED:
        return LLMRoute(
            provider="litellm",
            model=provider_default_model("litellm", use_premium=use_premium),
        )
    if use_premium:
        model = settings.PREMIUM_LLM
        provider = (
            "anthropic"
            if model.lower().startswith(("claude", "anthropic"))
            else "deepseek"
        )
        return LLMRoute(provider=provider, model=model)
    return LLMRoute(provider="deepseek", model=settings.PRIMARY_LLM)


def route_from_values(
    provider: str | None,
    model: str | None,
    *,
    use_premium: bool,
) -> LLMRoute | None:
    provider = _clean(provider)
    model = _clean(model)
    if not provider and not model:
        return None
    if not provider:
        provider = fallback_route(use_premium).provider
    return LLMRoute(
        provider=provider,
        model=model or provider_default_model(provider, use_premium=use_premium),
    )


def default_platform_llm_config() -> dict[str, str | None]:
    standard = fallback_route(False)
    premium = fallback_route(True)
    return {
        "standard_provider": standard.provider,
        "standard_model": standard.model,
        "premium_provider": premium.provider,
        "premium_model": premium.model,
    }


def _normalize_config(value: dict[str, Any] | None) -> dict[str, str | None]:
    config = default_platform_llm_config()
    if not value:
        return config
    for key in config:
        if key in value:
            config[key] = _clean(value.get(key))
    return config


async def get_platform_llm_config(db: AsyncSession) -> dict[str, str | None]:
    result = await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == LLM_ROUTING_KEY)
    )
    row = result.scalar_one_or_none()
    return _normalize_config(row.value if row else None)


async def upsert_platform_llm_config(
    db: AsyncSession,
    updates: dict[str, str | None],
) -> dict[str, str | None]:
    result = await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == LLM_ROUTING_KEY)
    )
    row = result.scalar_one_or_none()
    current = _normalize_config(row.value if row else None)
    for key, value in updates.items():
        if key in current:
            current[key] = _clean(value)

    if row is None:
        row = PlatformSetting(key=LLM_ROUTING_KEY, value=current)
        db.add(row)
    else:
        row.value = current
        row.updated_at = datetime.now(timezone.utc)
    return current


async def resolve_llm_route(
    db: AsyncSession,
    tenant_id,
    *,
    use_premium: bool,
    requested_provider: str = "default",
    requested_model: str | None = None,
) -> LLMRoute:
    requested_provider = _clean(requested_provider) or "default"
    if requested_provider != "default":
        return route_from_values(
            requested_provider,
            requested_model,
            use_premium=use_premium,
        ) or fallback_route(use_premium)

    ts_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    ts = ts_result.scalar_one_or_none()
    if ts:
        if use_premium:
            route = route_from_values(
                ts.premium_llm_provider,
                ts.premium_llm_model,
                use_premium=True,
            )
        else:
            route = route_from_values(
                ts.default_llm_provider,
                ts.default_llm_model,
                use_premium=False,
            )
        if route:
            return route

    platform_config = await get_platform_llm_config(db)
    if use_premium:
        route = route_from_values(
            platform_config.get("premium_provider"),
            platform_config.get("premium_model"),
            use_premium=True,
        )
    else:
        route = route_from_values(
            platform_config.get("standard_provider"),
            platform_config.get("standard_model"),
            use_premium=False,
        )
    return route or fallback_route(use_premium)
