"""Expertise-aware caching service for RAG and LLM responses."""

import json
import hashlib
from typing import Optional, Tuple, Any
from datetime import timedelta

import redis.asyncio as redis

from app.config import get_settings

settings = get_settings()

# Cache configuration by expertise level
CACHE_CONFIG = {
    "junior": {
        "ttl_rag": 3600,  # 1 hour - high cache value
        "ttl_llm": 1800,  # 30 min
        "ttl_matter": 7200,  # 2 hours
        "hit_ratio_target": 0.40,
    },
    "mid": {
        "ttl_rag": 1800,  # 30 min
        "ttl_llm": 900,  # 15 min
        "ttl_matter": 3600,  # 1 hour
        "hit_ratio_target": 0.25,
    },
    "senior": {
        "ttl_rag": 900,  # 15 min - lower cache value
        "ttl_llm": 300,  # 5 min
        "ttl_matter": 1800,  # 30 min
        "hit_ratio_target": 0.10,
    },
}

# Skill-based cache multipliers (some skills benefit more from caching)
SKILL_CACHE_MULTIPLIER = {
    "commercial-legal": 1.5,  # Contract review highly cacheable
    "employment-legal": 1.3,  # Routine compliance cacheable
    "regulatory-legal": 1.3,  # Regulatory patterns repeat
    "litigation-legal": 0.7,  # Novel cases less cacheable
    "ip-legal": 0.8,  # FTO analysis less cacheable
    "privacy-legal": 1.0,  # DSAR, DPA moderately cacheable
    "product-legal": 0.9,  # Launch reviews less cacheable
    "ai-governance-legal": 0.6,  # Very novel, low cache value
}


class ExpertiseCacheManager:
    """Manages expertise-aware caching of RAG and LLM results."""

    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self.cache_enabled = bool(settings.REDIS_URL)

    async def init(self) -> None:
        """Initialize Redis connection."""
        if self.cache_enabled:
            try:
                self.redis_client = await redis.from_url(
                    settings.REDIS_URL, decode_responses=True
                )
                await self.redis_client.ping()
            except Exception as e:
                print(f"Warning: Redis cache unavailable: {e}")
                self.cache_enabled = False

    async def close(self) -> None:
        """Close Redis connection."""
        if self.redis_client:
            await self.redis_client.close()

    def _get_ttl(
        self,
        expertise_level: str,
        cache_type: str,
        skill: Optional[str] = None,
    ) -> int:
        """Calculate TTL based on expertise level and skill."""
        config = CACHE_CONFIG.get(expertise_level, CACHE_CONFIG["mid"])
        ttl_key = f"ttl_{cache_type}"
        ttl = config.get(ttl_key, 900)

        # Apply skill multiplier
        if skill and skill in SKILL_CACHE_MULTIPLIER:
            multiplier = SKILL_CACHE_MULTIPLIER[skill]
            ttl = int(ttl * multiplier)

        return ttl

    def _make_key(self, prefix: str, *parts: str) -> str:
        """Create a cache key from parts."""
        key_str = f"{prefix}:{'|'.join(str(p) for p in parts)}"
        # Hash long keys
        if len(key_str) > 200:
            hash_val = hashlib.md5(key_str.encode()).hexdigest()
            return f"{prefix}:{hash_val}"
        return key_str

    def _make_query_key(self, query: str, tenant_id: str, user_id: str) -> str:
        """Create a normalized cache key for a query."""
        query_hash = hashlib.md5(query.lower().strip().encode()).hexdigest()
        return self._make_key("query", tenant_id, user_id, query_hash)

    async def get_cached_rag_results(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        skill: Optional[str] = None,
    ) -> Optional[Tuple[str, list]]:
        """
        Retrieve cached RAG results.
        Returns (context_str, chunks) or None if not cached.
        """
        if not self.cache_enabled or not self.redis_client:
            return None

        try:
            key = self._make_key("rag", tenant_id, user_id, question[:50])
            cached = await self.redis_client.get(key)
            if cached:
                data = json.loads(cached)
                return data["context"], data["chunks"]
            return None
        except Exception as e:
            print(f"Cache get error: {e}")
            return None

    async def set_cached_rag_results(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        context_str: str,
        chunks: list,
        expertise_level: str = "mid",
        skill: Optional[str] = None,
    ) -> bool:
        """Cache RAG results with expertise-aware TTL."""
        if not self.cache_enabled or not self.redis_client:
            return False

        try:
            key = self._make_key("rag", tenant_id, user_id, question[:50])
            ttl = self._get_ttl(expertise_level, "rag", skill)
            data = {"context": context_str, "chunks": chunks}
            await self.redis_client.setex(
                key, ttl, json.dumps(data, default=str)
            )
            return True
        except Exception as e:
            print(f"Cache set error: {e}")
            return False

    async def get_cached_llm_response(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        context_hash: str,
        skill: Optional[str] = None,
    ) -> Optional[str]:
        """
        Retrieve cached LLM response.
        context_hash = hash of context_str to avoid false hits.
        """
        if not self.cache_enabled or not self.redis_client:
            return None

        try:
            key = self._make_key(
                "llm", tenant_id, user_id, question[:50], context_hash[:16]
            )
            cached = await self.redis_client.get(key)
            return cached
        except Exception as e:
            print(f"Cache get error: {e}")
            return None

    async def set_cached_llm_response(
        self,
        question: str,
        tenant_id: str,
        user_id: str,
        context_hash: str,
        response: str,
        expertise_level: str = "mid",
        skill: Optional[str] = None,
    ) -> bool:
        """Cache LLM response with expertise-aware TTL."""
        if not self.cache_enabled or not self.redis_client:
            return False

        try:
            key = self._make_key(
                "llm", tenant_id, user_id, question[:50], context_hash[:16]
            )
            ttl = self._get_ttl(expertise_level, "llm", skill)
            await self.redis_client.setex(key, ttl, response)
            return True
        except Exception as e:
            print(f"Cache set error: {e}")
            return False

    async def get_cached_matter_context(
        self,
        matter_id: str,
        tenant_id: str,
    ) -> Optional[str]:
        """Retrieve cached matter context."""
        if not self.cache_enabled or not self.redis_client:
            return None

        try:
            key = self._make_key("matter", tenant_id, matter_id)
            cached = await self.redis_client.get(key)
            return cached
        except Exception as e:
            print(f"Cache get error: {e}")
            return None

    async def set_cached_matter_context(
        self,
        matter_id: str,
        tenant_id: str,
        context: str,
        expertise_level: str = "mid",
    ) -> bool:
        """Cache matter context."""
        if not self.cache_enabled or not self.redis_client:
            return False

        try:
            key = self._make_key("matter", tenant_id, matter_id)
            ttl = self._get_ttl(expertise_level, "matter")
            await self.redis_client.setex(key, ttl, context)
            return True
        except Exception as e:
            print(f"Cache set error: {e}")
            return False

    async def invalidate_user_cache(
        self,
        tenant_id: str,
        user_id: str,
        cache_type: Optional[str] = None,
    ) -> bool:
        """
        Invalidate all cache for a user.
        cache_type: "rag" | "llm" | "matter" | None (all)
        """
        if not self.cache_enabled or not self.redis_client:
            return False

        try:
            pattern = f"*{tenant_id}|{user_id}*"
            if cache_type:
                pattern = f"{cache_type}:{tenant_id}|{user_id}*"

            keys = await self.redis_client.keys(pattern)
            if keys:
                await self.redis_client.delete(*keys)
            return True
        except Exception as e:
            print(f"Cache invalidate error: {e}")
            return False

    def get_cache_config(self, expertise_level: str) -> dict:
        """Get cache configuration for expertise level."""
        return CACHE_CONFIG.get(expertise_level, CACHE_CONFIG["mid"])
