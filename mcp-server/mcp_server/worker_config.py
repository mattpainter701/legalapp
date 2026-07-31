from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MODEL = "mixedbread-ai/mxbai-embed-large-v1"
DEFAULT_DIM = 1024
DEFAULT_BATCH_SIZE = 32


@dataclass(frozen=True)
class WorkerConfig:
    worker_id: int
    total_workers: int
    batch_size: int = DEFAULT_BATCH_SIZE
    model: str = DEFAULT_MODEL
    dim: int = DEFAULT_DIM
    db_url: str | None = None

    def validate(self) -> None:
        if self.worker_id < 0 or self.worker_id >= self.total_workers:
            raise ValueError("worker_id must be between 0 and total_workers - 1")
        if self.total_workers <= 0:
            raise ValueError("total_workers must be positive")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.dim != DEFAULT_DIM:
            raise ValueError("mxbai CourtListener embeddings must be 1024-dimensional")


def partition_sql(corpus: str = "opinion_chunks") -> str:
    if corpus == "opinion_chunks":
        return """
            SELECT id, content
            FROM opinion_chunks
            WHERE embedding IS NULL
              AND ABS(HASHTEXT(id::text)) %% %s = %s
            ORDER BY created_at, id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """
    if corpus == "legal_document_chunks":
        return """
            SELECT c.id,
                   CONCAT(
                       '[', COALESCE(d.jurisdiction, 'unknown'), '] ',
                       '[', d.authority_tier, '] ', d.title, E'\n', c.content
                   ) AS content,
                   d.source_key
            FROM legal_document_chunks c
            JOIN legal_documents d ON d.id = c.document_id
            JOIN legal_sources s ON s.source_key = d.source_key
            WHERE c.embedding IS NULL
              AND s.storage_policy IN ('mirror', 'normalized_text')
              AND ABS(HASHTEXT(c.id::text)) %% %s = %s
            ORDER BY c.created_at, c.id
            LIMIT %s
            FOR UPDATE OF c SKIP LOCKED
        """
    raise ValueError(f"unsupported embedding corpus: {corpus}")
