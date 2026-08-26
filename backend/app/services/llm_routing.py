import re
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.platform import PlatformSetting
from app.models.llm_routing_profile import LLMRoutingProfile
from app.models.tenant import TenantSettings

settings = get_settings()

LLM_ROUTING_KEY = "llm_routing"
LLM_ROUTE_CONFIG_KEY = "llm_route_config_v2"
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
ROUTE_CACHE_TTL_SECONDS = 30.0
_route_cache: dict[tuple[str, bool, str, str | None], tuple[float, "LLMRoute"]] = {}


@dataclass(frozen=True)
class LLMRoute:
    requested_route: str
    resolved_route: str
    gateway_alias: str
    gateway_provider: str = LITELLM_PROVIDER
    customer_api_key: str | None = None
    customer_provider: str | None = None
    customer_endpoint: str | None = None

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


def invalidate_llm_route_cache(tenant_id: Any | None = None) -> None:
    """Clear cached route resolutions after route-affecting settings change."""
    if tenant_id is None:
        _route_cache.clear()
        return

    tenant_key = str(tenant_id)
    stale_keys = [key for key in _route_cache if key[0] == tenant_key]
    for key in stale_keys:
        _route_cache.pop(key, None)


def _route_cache_key(
    tenant_id: Any,
    *,
    use_premium: bool,
    requested_provider: str,
    requested_model: str | None,
) -> tuple[str, bool, str, str | None]:
    return (
        str(tenant_id),
        use_premium,
        (_clean(requested_provider) or "default").lower(),
        _clean(requested_model),
    )


def _get_cached_route(
    cache_key: tuple[str, bool, str, str | None],
) -> LLMRoute | None:
    cached = _route_cache.get(cache_key)
    if not cached:
        return None

    expires_at, route = cached
    if expires_at <= monotonic():
        _route_cache.pop(cache_key, None)
        return None
    return route


def _set_cached_route(
    cache_key: tuple[str, bool, str, str | None],
    route: LLMRoute,
) -> LLMRoute:
    _route_cache[cache_key] = (monotonic() + ROUTE_CACHE_TTL_SECONDS, route)
    return route


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


def _current_managed_alias(
    alias: str | None, platform_config: dict[str, str | None]
) -> str | None:
    """Move logical tenant overrides forward with managed route revisions."""

    alias = _clean(alias)
    if not alias:
        return None
    if alias == "clarity-standard" or alias.startswith("clarity-standard-r"):
        return _clean(platform_config.get("standard_model")) or alias
    if alias == "clarity-premium" or alias.startswith("clarity-premium-r"):
        return _clean(platform_config.get("premium_model")) or alias
    return alias


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


async def get_tenant_routing_profile(
    db: AsyncSession, tenant_id: Any
) -> LLMRoutingProfile | None:
    """Resolve an assigned active profile, falling back to the active default."""
    ts = await db.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    if ts and ts.llm_routing_profile_id:
        assigned = await db.scalar(
            select(LLMRoutingProfile).where(
                LLMRoutingProfile.id == ts.llm_routing_profile_id,
                LLMRoutingProfile.is_active.is_(True),
            )
        )
        if assigned and assigned.assignable:
            return assigned
    default_profile = await db.scalar(
        select(LLMRoutingProfile)
        .where(
            LLMRoutingProfile.is_default.is_(True),
            LLMRoutingProfile.is_active.is_(True),
        )
        .order_by(LLMRoutingProfile.updated_at.desc())
        .limit(1)
    )
    return default_profile if default_profile and default_profile.assignable else None


async def standard_matter_context_allowed(db: AsyncSession, tenant_id: Any) -> bool:
    """Return the operator-approved data policy for the managed Standard route.

    This deliberately defaults to false so legacy installations preserve the
    public/general-only Standard boundary until an operator explicitly enables
    confidential matter context from Platform > AI Provider Routing.
    """
    profile = await get_tenant_routing_profile(db, tenant_id)
    if profile is not None:
        return bool(profile.standard_allow_matter_context)
    result = await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == LLM_ROUTE_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    config = row.value if row and isinstance(row.value, dict) else {}
    standard = config.get("standard")
    return bool(isinstance(standard, dict) and standard.get("allow_matter_context"))


async def route_matter_context_allowed(
    db: AsyncSession,
    tenant_id: Any,
    *,
    use_premium: bool,
    route: LLMRoute | None = None,
) -> bool:
    """Return whether the resolved route may receive confidential matter data.

    A profile policy approves only that profile's activated, validated alias. It
    must not spill over to an explicit model, a legacy tenant override, or a
    customer BYOK route that Platform did not validate as part of the profile.
    Those routes remain fail-closed until they gain their own independently
    reviewed confidential-data policy.
    """

    if route is not None:
        expected_profile_route = (
            "profile-premium" if use_premium else "profile-standard"
        )
        independently_unapproved_routes = {
            "customer",
            "explicit-standard",
            "explicit-premium",
            "tenant-standard",
            "tenant-premium",
        }
        if route.resolved_route in independently_unapproved_routes:
            return False

        # A profile flag applies only to the matching activated profile tier.
        if route.resolved_route.startswith("profile-") and (
            route.resolved_route != expected_profile_route
        ):
            return False

    profile = await get_tenant_routing_profile(db, tenant_id)
    if profile is not None and (
        route is None
        or route.resolved_route
        == ("profile-premium" if use_premium else "profile-standard")
    ):
        return bool(
            profile.premium_allow_matter_context
            if use_premium
            else profile.standard_allow_matter_context
        )
    return True if use_premium else await standard_matter_context_allowed(db, tenant_id)


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
    invalidate_llm_route_cache()
    return current


async def resolve_llm_route(
    db: AsyncSession,
    tenant_id,
    *,
    use_premium: bool,
    requested_provider: str = "default",
    requested_model: str | None = None,
) -> LLMRoute:
    cache_key = _route_cache_key(
        tenant_id,
        use_premium=use_premium,
        requested_provider=requested_provider,
        requested_model=requested_model,
    )
    cached_route = _get_cached_route(cache_key)
    if cached_route:
        return cached_route

    requested_route = _normalize_requested_route(
        requested_provider,
        use_premium=use_premium,
    )

    explicit_alias = _clean(requested_model)
    requested_provider_clean = (_clean(requested_provider) or "default").lower()
    if explicit_alias and requested_provider_clean in {"default", LITELLM_PROVIDER}:
        return _set_cached_route(
            cache_key,
            LLMRoute(
                requested_route=requested_route,
                resolved_route=(
                    "explicit-premium" if use_premium else "explicit-standard"
                ),
                gateway_alias=explicit_alias,
            ),
        )

    ts_result = await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    ts = ts_result.scalar_one_or_none()

    # Customer-supplied LLM (BYOK): tenant opts in with their own API key/endpoint
    # for Gemini or Copilot (Azure OpenAI). These requests bypass the LiteLLM
    # gateway entirely and talk directly to the tenant's own provider account —
    # the gateway_alias must be a model/deployment name THAT PROVIDER recognizes,
    # not a LiteLLM gateway alias.
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
            customer_provider = ts.customer_llm_provider
            deployment = _clean(ts.customer_llm_config.get("deployment"))
            endpoint = _clean(ts.customer_llm_config.get("endpoint"))
            gateway_alias = (
                deployment
                or _clean(ts.default_llm_model)
                or (
                    "gemini-2.0-flash"
                    if customer_provider == "gemini"
                    else customer_provider
                )
            )
            return _set_cached_route(
                cache_key,
                LLMRoute(
                    requested_route=requested_route,
                    resolved_route="customer",
                    gateway_alias=gateway_alias,
                    gateway_provider=customer_provider,
                    customer_api_key=raw_key,
                    customer_provider=customer_provider,
                    customer_endpoint=endpoint,
                ),
            )

    platform_config = await get_platform_llm_config(db)
    profile = await get_tenant_routing_profile(db, tenant_id)
    profile_aliases = (
        (profile.activation or {}).get("aliases", {})
        if profile and isinstance(profile.activation, dict)
        else {}
    )
    profile_alias = (
        profile_aliases.get("premium")
        if requested_route in {"premium", "tenant-premium"}
        else profile_aliases.get("standard")
    )
    if profile_alias:
        return _set_cached_route(
            cache_key,
            LLMRoute(
                requested_route=requested_route,
                resolved_route="profile-premium" if use_premium else "profile-standard",
                gateway_alias=profile_alias,
            ),
        )
    if ts:
        if requested_route in {"premium", "tenant-premium"}:
            alias = _model_from_values(ts.premium_llm_provider, ts.premium_llm_model)
            alias = _current_managed_alias(alias, platform_config)
            if alias:
                return _set_cached_route(
                    cache_key,
                    LLMRoute(
                        requested_route=requested_route,
                        resolved_route="tenant-premium",
                        gateway_alias=alias,
                    ),
                )
        elif requested_route in {"standard", "tenant-standard"}:
            alias = _model_from_values(ts.default_llm_provider, ts.default_llm_model)
            alias = _current_managed_alias(alias, platform_config)
            if alias:
                return _set_cached_route(
                    cache_key,
                    LLMRoute(
                        requested_route=requested_route,
                        resolved_route="tenant-standard",
                        gateway_alias=alias,
                    ),
                )

    if requested_route in {"premium", "tenant-premium"}:
        return _set_cached_route(
            cache_key,
            LLMRoute(
                requested_route=requested_route,
                resolved_route="premium",
                gateway_alias=(
                    profile_aliases.get("premium")
                    or platform_config.get("premium_model")
                    or settings.LITELLM_PREMIUM_MODEL
                ),
            ),
        )
    return _set_cached_route(
        cache_key,
        LLMRoute(
            requested_route=requested_route,
            resolved_route="standard",
            gateway_alias=(
                profile_aliases.get("standard")
                or platform_config.get("standard_model")
                or settings.LITELLM_STANDARD_MODEL
            ),
        ),
    )


@event.listens_for(TenantSettings, "after_insert")
@event.listens_for(TenantSettings, "after_update")
@event.listens_for(TenantSettings, "after_delete")
def _invalidate_route_cache_on_tenant_settings_write(mapper, connection, target):
    invalidate_llm_route_cache(target.tenant_id)


@event.listens_for(LLMRoutingProfile, "after_insert")
@event.listens_for(LLMRoutingProfile, "after_update")
@event.listens_for(LLMRoutingProfile, "after_delete")
def _invalidate_route_cache_on_profile_write(mapper, connection, target):
    invalidate_llm_route_cache()


# ── Query Complexity Classifier ────────────────────────────────────────────

# Simple query patterns — route to standard/free models
SIMPLE_PATTERNS = [
    r"^(what|who|when|where)\s+(is|are|was|were)\s",  # definition queries
    r"^(define|definition of)\s",
    r"^\d+[\+\-\*\/]\d+",  # math
    r"^(hi|hello|hey|thanks|thank you|ok|okay|yes|no)[\s!.?]*$",  # small talk
    r"^how\s+(many|much|long|old)\s",  # factual lookup
    r"^(can|could|would|will|do|does|did|is|are|was|were)\s+\w+\s",  # yes/no
]

# Complex query indicators — route to premium (or best available)
COMPLEX_PATTERNS = [
    r"\b(draft|prepare|write|compose|generate)\b.*\b(contract|agreement|motion|brief|pleading|letter|document|clause)\b",
    r"\b(analy[sz]e|review|evaluate|assess|compare|contrast)\b",
    r"\b(summarize|summarise|outline|explain)\b.*\b(case|ruling|opinion|statute|regulation)\b",
    r"\b(multi.?jurisdict|conflict of law|choice of law)\b",
    r"\b(what are the (elements|factors|requirements|grounds|defenses))\b",
]


def classify_query_complexity(query: str) -> str:
    """Classify a user query as 'simple' or 'complex' for model routing.

    Simple queries are short definition/fact/math/small-talk questions that
    any model can handle. Complex queries involve drafting, analysis, or
    multi-hop legal reasoning that benefits from a better model.

    Returns 'simple' or 'complex'.
    """
    query_lower = query.strip().lower()
    query_len = len(query_lower)

    # Very short queries are simple
    if query_len < 30:
        return "simple"

    # Check complex patterns first (higher specificity)
    for pattern in COMPLEX_PATTERNS:
        if re.search(pattern, query_lower):
            return "complex"

    # Check simple patterns
    for pattern in SIMPLE_PATTERNS:
        if re.search(pattern, query_lower):
            return "simple"

    # Default: longer queries or queries with legal terms → complex
    if query_len > 200:
        return "complex"

    legal_terms = [
        "statute",
        "regulation",
        "liability",
        "damages",
        "negligence",
        "contract",
        "breach",
        "plaintiff",
        "defendant",
        "jurisdiction",
        "tort",
        "fiduciary",
        "injunction",
        "discovery",
        "appeal",
        "motion",
        "pleading",
        "testament",
        "probate",
        "custody",
    ]
    if any(term in query_lower for term in legal_terms):
        return "complex"

    return "simple"


# ── Model Latency Tracker ──────────────────────────────────────────────────


@dataclass
class ModelLatencySample:
    model: str
    latency_ms: float
    timestamp: float  # monotonic seconds


# Per-model latency ring buffer (last N samples)
LATENCY_BUFFER_SIZE = 8
_latency_buffers: dict[str, list[ModelLatencySample]] = {}

# Models currently in cooldown (slow → skip)
_cooldown_until: dict[str, float] = {}

# Thresholds
LATENCY_WARN_MS = 8000  # 8s — mark as slow
LATENCY_COOLDOWN_MS = 15000  # 15s — cooldown for 5 minutes
COOLDOWN_DURATION_S = 300  # 5 minutes
SLOW_RATIO_THRESHOLD = 0.5  # >50% of recent samples slow → cooldown


def record_model_latency(model_alias: str, latency_ms: float) -> None:
    """Record a latency sample for a model alias."""
    if model_alias not in _latency_buffers:
        _latency_buffers[model_alias] = []
    buf = _latency_buffers[model_alias]
    buf.append(ModelLatencySample(model_alias, latency_ms, monotonic()))
    if len(buf) > LATENCY_BUFFER_SIZE:
        buf.pop(0)

    # Check if model should enter cooldown
    if len(buf) >= 3:
        slow_count = sum(1 for s in buf[-6:] if s.latency_ms > LATENCY_COOLDOWN_MS)
        if slow_count >= 2:
            _cooldown_until[model_alias] = monotonic() + COOLDOWN_DURATION_S

        # Also check ratio-based cooldown (consistently slow)
        recent = buf[-4:]
        slow_ratio = sum(1 for s in recent if s.latency_ms > LATENCY_WARN_MS) / len(
            recent
        )
        if slow_ratio >= SLOW_RATIO_THRESHOLD:
            _cooldown_until[model_alias] = monotonic() + COOLDOWN_DURATION_S


def is_model_in_cooldown(model_alias: str) -> bool:
    """Check if a model is currently in cooldown (too slow)."""
    until = _cooldown_until.get(model_alias)
    if until is None:
        return False
    if monotonic() > until:
        _cooldown_until.pop(model_alias, None)
        return False
    return True


def get_model_latency_stats(model_alias: str) -> dict | None:
    """Get latency stats for a model alias. Returns None if no data."""
    buf = _latency_buffers.get(model_alias)
    if not buf:
        return None
    latencies = [s.latency_ms for s in buf[-6:]]
    return {
        "model": model_alias,
        "avg_ms": round(sum(latencies) / len(latencies)),
        "p50_ms": round(sorted(latencies)[len(latencies) // 2]),
        "p95_ms": round(
            sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        ),
        "samples": len(latencies),
        "in_cooldown": is_model_in_cooldown(model_alias),
    }


def get_slow_models() -> list[str]:
    """Return list of model aliases currently in cooldown."""
    return [m for m in list(_cooldown_until) if is_model_in_cooldown(m)]
