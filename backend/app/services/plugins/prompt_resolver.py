"""Prompt resolution: tenant override -> code default -> generic fallback."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import PromptOverride
from app.services.cache import ExpertiseCacheManager


class PromptResolver:
    """Resolves prompts with cache-aware lookup.

    Resolution order:
    1. Redis cache hit -> return cached
    2. DB tenant override (is_active) -> cache + return
    3. Code default (ALL_DEFAULT_PROMPTS) -> cache (short TTL) + return
    4. None (caller falls back to generic prompt)
    """

    def __init__(self, cache_manager: ExpertiseCacheManager):
        self.cache = cache_manager

    async def get_prompt(
        self, db: AsyncSession, tenant_id: str, plugin: str, skill: str
    ) -> Optional[str]:
        """Resolve prompt: tenant override -> code default -> None."""
        # 1. Check Redis cache
        if self.cache.cache_enabled:
            cached = await self.cache.get_cached_prompt(tenant_id, plugin, skill)
            if cached is not None:
                return cached

        # 2. Check DB for active tenant override
        result = await db.execute(
            select(PromptOverride).where(
                PromptOverride.tenant_id == tenant_id,
                PromptOverride.plugin_name == plugin,
                PromptOverride.skill_name == skill,
                PromptOverride.is_active,
            )
        )
        override = result.scalar_one_or_none()
        if override:
            prompt = override.prompt_content
            if self.cache.cache_enabled:
                await self.cache.set_cached_prompt(
                    tenant_id, plugin, skill, prompt, ttl=self.cache.PROMPT_CACHE_TTL
                )
            return prompt

        # 3. Check code defaults
        from app.services.plugins.prompts import ALL_DEFAULT_PROMPTS

        default = ALL_DEFAULT_PROMPTS.get((plugin, skill))
        if default:
            if self.cache.cache_enabled:
                await self.cache.set_cached_prompt(
                    tenant_id,
                    plugin,
                    skill,
                    default,
                    ttl=self.cache.DEFAULT_PROMPT_CACHE_TTL,
                )
            return default

        return None  # triggers generic fallback

    async def invalidate(
        self, tenant_id: str, plugin: Optional[str] = None, skill: Optional[str] = None
    ) -> None:
        """Invalidate cached prompt after override update."""
        if self.cache.cache_enabled:
            await self.cache.invalidate_prompt_cache(tenant_id, plugin, skill)
