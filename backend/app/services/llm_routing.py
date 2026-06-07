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
LITELLM_PROVIDER = "litellm"
VALID_LLM_PROVIDERS = {LITELLM_PROVIDER}
VALID_LLM_ROUTES = {"standard", "premium", "tenant-standard", "tenant-premium"}
LEGACY_DIRECT_PROVIDERS = {
    "deepseek",
    "opencode",
    "openrouter",
    "anthropic",
    "azure",
    "gemini",
}


@dataclass(frozen=True)
class LLMRoute:
    requested_route: str
    resolved_route: str
    gateway_alias: str
    gateway_provider: str = LITELLM_PROVIDER
    customer_api_key: str | None = None

    @property
    def provider(self) -> str:
        return self.gateway_provider

    @property
    def model(self) -> str:
        return self.gateway_alias

    @property
    def cache_key(self) -> str:
        return f"{self.gateway_provider}:{self.gateway_alias}"


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def provider_default_model(provider: str, use_premium: bool = False) -> str:
    """Compatibility wrapper: every route resolves to a LiteLLM alias."""
    return (
        settings.LITELLM_PREMIUM_MODEL
        if use_premium
        else settings.LITELLM_STANDARD_MODEL
    )


def fallback_route(use_premium: bool) -> LLMRoute:
    route_name = "premium" if use_premium else "standard"
    return LLMRoute(
        requested_route=route_name,
        resolved_route=route_name,
        gateway_alias=provider_default_model(LITELLM_PROVIDER, use_premium),
    )


def _normalize_requested_route(requested: str | None, *, use_premium: bool) -> str:
    requested = (_clean(requested) or "default").lower()
    if requested in {"default", LITELLM_PROVIDER}:
        return "premium" if use_premium else "standard"
    if requested in VALID_LLM_ROUTES:
        return requested
    if requested in LEGACY_DIRECT_PROVIDERS:
        return "premium" if use_premium else "standard"
    return "premium" if use_premium else "standard"


def _model_from_values(provider: str | None, model: str | None) -> str | None:
    provider = (_clean(provider) or LITELLM_PROVIDER).lower()
    if provider != LITELLM_PROVIDER:
        return None
    return _clean(model)


def route_from_values(
    provider: str | None,
    model: str | None,
    *,
    use_premium: bool,
) -> LLMRoute | None:
    alias = _model_from_values(provider, model)
    if not alias:
        return None
    route_name = "premium" if use_premium else "standard"
    return LLMRoute(
        requested_route=route_name,
        resolved_route=route_name,
        gateway_alias=alias,
    )


def default_platform_llm_config() -> dict[str, str | None]:
    return {
        "standard_provider": LITELLM_PROVIDER,
        "standard_model": settings.LITELLM_STANDARD_MODEL,
        "premium_provider": LITELLM_PROVIDER,
        "premium_model": settings.LITELLM_PREMIUM_MODEL,
    }


def _normalize_config(value: dict[str, Any] | None) -> dict[str, str | None]:
    config = default_platform_llm_config()
    if not value:
        return config
    standard_provider = _clean(value.get("standard_provider"))
    premium_provider = _clean(value.get("premium_provider"))
    if standard_provider in (None, LITELLM_PROVIDER):
        config["standard_model"] = (
            _clean(value.get("standard_model")) or settings.LITELLM_STANDARD_MODEL
        )
    if premium_provider in (None, LITELLM_PROVIDER):
        config["premium_model"] = (
            _clean(value.get("premium_model")) or settings.LITELLM_PREMIUM_MODEL
        )
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
        if key in {"standard_provider", "premium_provider"}:
            current[key] = LITELLM_PROVIDER
        elif key in current:
            current[key] = _clean(value)

    current = _normalize_config(current)
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
    requested_route = _normalize_requested_route(
        requested_provider,
        use_premium=use_premium,
    )

    explicit_alias = _clean(requested_model)
    requested_provider_clean = (_clean(requested_provider) or "default").lower()
    if explicit_alias and requested_provider_clean in {"default", LITELLM_PROVIDER}:
        return LLMRoute(
            requested_route=requested_route,
            resolved_route=requested_route,
            gateway_alias=explicit_alias,
        )

    ts_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    ts = ts_result.scalar_one_or_none()

    # Customer-supplied LLM: tenant opts in with their own API key for Gemini/Copilot.
    # The encrypted key is stored in customer_llm_config["encrypted_api_key"] and
    # forwarded to the LiteLLM gateway as a per-request api_key override.
    if (
        ts
        and ts.use_customer_llm
        and ts.customer_llm_provider
        and ts.customer_llm_config
    ):
        from app.services.token_vault import decrypt_token

        raw_key = ""
        try:
            raw_key = decrypt_token(ts.customer_llm_config.get("encrypted_api_key", ""))
        except Exception:
            pass
        if raw_key:
            gateway_alias = _clean(ts.default_llm_model) or ts.customer_llm_provider
            return LLMRoute(
                requested_route=requested_route,
                resolved_route="customer",
                gateway_alias=gateway_alias,
                customer_api_key=raw_key,
            )

    if ts:
        if requested_route in {"premium", "tenant-premium"}:
            alias = _model_from_values(ts.premium_llm_provider, ts.premium_llm_model)
            if alias:
                return LLMRoute(
                    requested_route=requested_route,
                    resolved_route="tenant-premium",
                    gateway_alias=alias,
                )
        elif requested_route in {"standard", "tenant-standard"}:
            alias = _model_from_values(ts.default_llm_provider, ts.default_llm_model)
            if alias:
                return LLMRoute(
                    requested_route=requested_route,
                    resolved_route="tenant-standard",
                    gateway_alias=alias,
                )

    platform_config = await get_platform_llm_config(db)
    if requested_route in {"premium", "tenant-premium"}:
        return LLMRoute(
            requested_route=requested_route,
            resolved_route="premium",
            gateway_alias=(
                platform_config.get("premium_model") or settings.LITELLM_PREMIUM_MODEL
            ),
        )
    return LLMRoute(
        requested_route=requested_route,
        resolved_route="standard",
        gateway_alias=(
            platform_config.get("standard_model") or settings.LITELLM_STANDARD_MODEL
        ),
    )
