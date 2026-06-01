from typing import List
import logging
from openai import AsyncOpenAI

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self):
        # Use OpenAI key, or fall back to DeepSeek/OpenCode key for providers
        # that expose OpenAI-compatible embeddings (e.g. opencode.ai)
        api_key = (
            settings.OPENAI_API_KEY
            or settings.OPENCODE_KEY
            or settings.DEEPSEEK_API_KEY
        )
        if not api_key:
            self.client = None
            self.model = None
        else:
            # If using an OpenAI key, use OpenAI base; otherwise use the same
            # base URL as chat (DeepSeek/openCode compatible endpoint)
            if settings.OPENAI_API_KEY:
                self.client = AsyncOpenAI(api_key=api_key)
                self.model = settings.EMBEDDING_MODEL
            else:
                self.client = AsyncOpenAI(
                    api_key=api_key,
                    base_url=settings.DEEPSEEK_BASE_URL,
                )
                self.model = settings.EMBEDDING_MODEL

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
