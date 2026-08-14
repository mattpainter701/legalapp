import asyncio
import logging
from typing import List

from openai import AsyncOpenAI

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# The public BGE encoder is several hundred MB of resident weights. Routers each
# construct their own ``EmbeddingService`` at import time, so per-instance
# caching loaded one copy per router and let concurrent first-queries load it
# more than once. Cache the encoder on the module and serialize the load.
_PUBLIC_MODEL_LOCK = asyncio.Lock()
_PUBLIC_MODEL_STATE: dict[str, object | None] = {"model": None, "failed": False}


def _usable_provider_key(value: str | None) -> bool:
    """Reject empty/template notes before constructing a provider client.

    API tokens do not contain whitespace.  This also keeps descriptive values
    sometimes used in local hypervisor templates from being sent as credentials.
    """

    return bool(
        value and value.strip() == value and not any(char.isspace() for char in value)
    )


class EmbeddingService:
    def __init__(self):
        if settings.LITELLM_ENABLED and settings.LITELLM_EMBEDDING_MODEL:
            # Route embeddings through LiteLLM when a model alias is configured
            self.client = AsyncOpenAI(
                api_key=settings.LITELLM_API_KEY or "sk-local-litellm",
                base_url=settings.LITELLM_BASE_URL,
            )
            self.model = settings.LITELLM_EMBEDDING_MODEL
        else:
            # Direct provider fallback when LiteLLM is disabled. OpenCode and
            # DeepSeek chat credentials are deliberately excluded: their chat
            # endpoints do not implement the configured OpenAI embedding model.
            if _usable_provider_key(settings.OPENAI_API_KEY):
                self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                self.model = settings.EMBEDDING_MODEL
            elif _usable_provider_key(settings.OPENROUTER_API_KEY):
                self.client = AsyncOpenAI(
                    api_key=settings.OPENROUTER_API_KEY,
                    base_url=settings.OPENROUTER_BASE_URL,
                )
                self.model = settings.OPENROUTER_EMBEDDING_MODEL
            else:
                self.client = None
                self.model = None

        self.public_model = None
        self.public_model_load_failed = False

    async def embed_text(self, text: str) -> List[float] | None:
        """Embed a single text string. Returns None if embeddings unavailable."""
        if not self.client:
            return None
        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning("Embedding failed: %s", e)
            return None

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Batch embed texts, sending at most 100 per API call. Returns empty list if unavailable."""
        if not texts or not self.client:
            return []
        try:
            all_embeddings: List[List[float]] = []
            batch_size = 100

            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                response = await self.client.embeddings.create(
                    model=self.model,
                    input=batch,
                )
                sorted_data = sorted(response.data, key=lambda x: x.index)
                all_embeddings.extend([item.embedding for item in sorted_data])

            return all_embeddings
        except Exception as e:
            logger.warning("Batch embedding failed: %s", e)
            return []

    async def embed_public_query(self, text: str) -> List[float] | None:
        """Embed a public case-law query with BGE-small if dependencies exist."""
        if self.public_model_load_failed:
            return None

        try:
            model = await self._get_public_model()
            if model is None:
                return None

            prefixed = (
                "Represent this sentence for searching relevant passages: " + text
            )
            embedding = await asyncio.to_thread(
                model.encode,
                prefixed,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()
            return list(embedding)
        except Exception as e:
            logger.warning("Public BGE query embedding failed: %s", e)
            return None

    async def _get_public_model(self):
        """Return the process-wide BGE encoder, loading it at most once."""

        if _PUBLIC_MODEL_STATE["model"] is not None:
            self.public_model = _PUBLIC_MODEL_STATE["model"]
            return self.public_model
        if _PUBLIC_MODEL_STATE["failed"]:
            self.public_model_load_failed = True
            return None

        async with _PUBLIC_MODEL_LOCK:
            # Re-check under the lock: a concurrent first-query may have
            # finished the load (or proved it impossible) while we waited.
            if _PUBLIC_MODEL_STATE["model"] is not None:
                self.public_model = _PUBLIC_MODEL_STATE["model"]
                return self.public_model
            if _PUBLIC_MODEL_STATE["failed"]:
                self.public_model_load_failed = True
                return None

            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                logger.info(
                    "sentence-transformers not installed; public CourtListener RAG disabled"
                )
                _PUBLIC_MODEL_STATE["failed"] = True
                self.public_model_load_failed = True
                return None

            try:
                model = await asyncio.to_thread(
                    SentenceTransformer,
                    settings.PUBLIC_EMBEDDING_MODEL,
                )
            except Exception as e:
                logger.warning("Failed to load public embedding model: %s", e)
                _PUBLIC_MODEL_STATE["failed"] = True
                self.public_model_load_failed = True
                return None

            _PUBLIC_MODEL_STATE["model"] = model
            self.public_model = model
            return model
