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


def partition_sql() -> str:
    return """
        SELECT id, content
        FROM opinion_chunks
        WHERE embedding IS NULL
          AND ABS(HASHTEXT(id::text)) %% %s = %s
        ORDER BY created_at, id
        LIMIT %s
        FOR UPDATE SKIP LOCKED
    """
