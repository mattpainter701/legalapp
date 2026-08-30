from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable

import httpx

from .worker_config import DEFAULT_MODEL

logger = logging.getLogger(__name__)

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingVector(list[float]):
    """List-compatible vector carrying the provider contract metadata."""

    model: str
    version: str
    dimension: int


def format_vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


@dataclass(frozen=True)
class QueryEmbeddingClient:
    url: str | None
    fallback_url: str | None = None
    model: str = DEFAULT_MODEL
    version: str = "1"
    dimension: int = 1024
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "QueryEmbeddingClient":
        timeout = float(os.environ.get("MCP_QUERY_EMBEDDING_TIMEOUT_SECONDS", "5"))
        return cls(
            url=os.environ.get("MCP_QUERY_EMBEDDING_URL") or os.environ.get("QUERY_EMBEDDING_URL"),
            fallback_url=os.environ.get("MCP_QUERY_EMBEDDING_FALLBACK_URL") or os.environ.get("QUERY_EMBEDDING_FALLBACK_URL"),
            model=os.environ.get("MCP_QUERY_EMBEDDING_MODEL", DEFAULT_MODEL),
            version=os.environ.get("MCP_QUERY_EMBEDDING_VERSION", "1"),
            dimension=int(os.environ.get("MCP_QUERY_EMBEDDING_DIMENSION", "1024")),
            timeout_seconds=timeout,
        )

    def embed_query(self, text: str) -> list[float] | None:
        if not self.url or not text.strip():
            return None

        payload = {"texts": [QUERY_PREFIX + text], "model": self.model}
        for endpoint in tuple(dict.fromkeys(url for url in (self.url, self.fallback_url) if url)):
            try:
                response = httpx.post(endpoint, json=payload, timeout=self.timeout_seconds)
                response.raise_for_status()
                data = response.json()
                embedding = data.get("embedding")
                if embedding is None:
                    embeddings = data.get("embeddings") or []
                    embedding = embeddings[0] if embeddings else None
                model = data.get("model") or self.model
                version = str(data.get("version") or data.get("embedding_version") or self.version)
                dimension = int(data.get("dimension") or len(embedding or []))
                if not embedding or model != self.model or version != self.version or dimension != self.dimension or len(embedding) != self.dimension:
                    raise ValueError("query embedding contract mismatch")
                result = EmbeddingVector(float(value) for value in embedding)
                result.model = model
                result.version = version
                result.dimension = dimension
                return result
            except Exception as exc:
                logger.warning("Query embedding provider failed: %s", exc)
        return None
