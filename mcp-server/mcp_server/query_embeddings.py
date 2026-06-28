from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable

import httpx

from .worker_config import DEFAULT_MODEL

logger = logging.getLogger(__name__)

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def format_vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


@dataclass(frozen=True)
class QueryEmbeddingClient:
    url: str | None
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "QueryEmbeddingClient":
        timeout = float(os.environ.get("MCP_QUERY_EMBEDDING_TIMEOUT_SECONDS", "5"))
        return cls(
            url=os.environ.get("MCP_QUERY_EMBEDDING_URL") or os.environ.get("QUERY_EMBEDDING_URL"),
            model=os.environ.get("MCP_QUERY_EMBEDDING_MODEL", DEFAULT_MODEL),
            timeout_seconds=timeout,
        )

    def embed_query(self, text: str) -> list[float] | None:
        if not self.url or not text.strip():
            return None

        payload = {"texts": [QUERY_PREFIX + text], "model": self.model}
        try:
            response = httpx.post(self.url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
            data = response.json()
            embedding = data.get("embedding")
            if embedding is None:
                embeddings = data.get("embeddings") or []
                embedding = embeddings[0] if embeddings else None
            if not embedding:
                return None
            return [float(value) for value in embedding]
        except Exception as exc:
            logger.warning("Query embedding provider failed: %s", exc)
            return None
