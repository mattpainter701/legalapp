"""Durable, index-free staging worker for legacy CourtListener opinions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Iterable

import psycopg2.extras

from .database import connect

DEFAULT_MODEL = "mixedbread-ai/mxbai-embed-large-v1"
DEFAULT_DIM = 1024
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
STAGE_TABLE = "opinion_embedding_backfill_stage"


@dataclass(frozen=True)
class OpinionBackfillConfig:
    worker_id: int
    total_workers: int
    batch_size: int
    db_url: str
    model: str = DEFAULT_MODEL
    model_version: int = 1
    dim: int = DEFAULT_DIM

    def validate(self) -> None:
        if self.total_workers <= 0 or not 0 <= self.worker_id < self.total_workers:
            raise ValueError("worker_id must be within total_workers")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if (
            self.model != DEFAULT_MODEL
            or self.model_version != 1
            or self.dim != DEFAULT_DIM
        ):
            raise ValueError(
                "opinion backfill requires mxbai v1 1024-dimensional embeddings"
            )
        if not self.db_url:
            raise ValueError("database URL is required")


@dataclass(frozen=True)
class BatchResult:
    selected: int
    staged: int
    select_seconds: float
    embed_seconds: float
    serialize_seconds: float
    write_seconds: float
    total_seconds: float

    @property
    def chunks_per_second(self) -> float:
        return self.staged / max(self.total_seconds, 0.001)

    def log_line(self, worker_id: int) -> str:
        payload = asdict(self)
        payload["chunks_per_second"] = round(self.chunks_per_second, 3)
        payload["worker_id"] = worker_id
        return "opinion_stage " + json.dumps(payload, sort_keys=True)


def selection_sql() -> str:
    return f"""
        SELECT oc.id, oc.content
        FROM opinion_chunks AS oc
        WHERE oc.embedding IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM {STAGE_TABLE} AS staged
              WHERE staged.chunk_id = oc.id
          )
        ORDER BY oc.created_at, oc.id
        LIMIT %s
        FOR UPDATE OF oc SKIP LOCKED
    """


def stage_insert_sql() -> str:
    return f"""
        INSERT INTO {STAGE_TABLE}
            (chunk_id, embedding, embedding_model, embedding_version,
             content_sha256, worker_id)
        VALUES %s
        ON CONFLICT (chunk_id) DO NOTHING
        RETURNING chunk_id
    """


def format_embedding(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def load_model(model_name: str):
    try:
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("sentence-transformers and torch are required") from exc
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return SentenceTransformer(model_name, device=device)


def embed_batch(model, texts: list[str], batch_size: int) -> list[list[float]]:
    prefixed = [QUERY_PREFIX + text for text in texts]
    vectors = model.encode(
        prefixed,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [
        vector.tolist() if hasattr(vector, "tolist") else list(vector)
        for vector in vectors
    ]


def process_once(config: OpinionBackfillConfig, model) -> BatchResult:
    config.validate()
    total_started = time.monotonic()
    with connect(config.db_url) as conn:
        with conn.cursor() as cur:
            select_started = time.monotonic()
            cur.execute(selection_sql(), [config.batch_size])
            rows = cur.fetchall()
            select_seconds = time.monotonic() - select_started
            if not rows:
                conn.rollback()
                total_seconds = time.monotonic() - total_started
                return BatchResult(0, 0, select_seconds, 0.0, 0.0, 0.0, total_seconds)

            chunk_ids = [row[0] for row in rows]
            texts = [row[1] for row in rows]
            embed_started = time.monotonic()
            vectors = embed_batch(model, texts, config.batch_size)
            embed_seconds = time.monotonic() - embed_started
            if len(vectors) != len(rows) or any(
                len(vector) != config.dim for vector in vectors
            ):
                raise RuntimeError("embedding output count or dimension mismatch")

            serialize_started = time.monotonic()
            values = [
                (
                    chunk_id,
                    format_embedding(vector),
                    config.model,
                    config.model_version,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    str(config.worker_id),
                )
                for chunk_id, text, vector in zip(chunk_ids, texts, vectors)
            ]
            serialize_seconds = time.monotonic() - serialize_started

            write_started = time.monotonic()
            inserted = psycopg2.extras.execute_values(
                cur,
                stage_insert_sql(),
                values,
                template="(%s, %s::vector, %s, %s, %s, %s)",
                page_size=len(values),
                fetch=True,
            )
            conn.commit()
            write_seconds = time.monotonic() - write_started

    return BatchResult(
        selected=len(rows),
        staged=len(inserted),
        select_seconds=select_seconds,
        embed_seconds=embed_seconds,
        serialize_seconds=serialize_seconds,
        write_seconds=write_seconds,
        total_seconds=time.monotonic() - total_started,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage legacy CourtListener opinion embeddings"
    )
    parser.add_argument("--worker-id", type=int, required=True)
    parser.add_argument("--total-workers", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--db-url",
        default=os.environ.get("VECTORDB_URL") or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--loop-interval", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = OpinionBackfillConfig(
        worker_id=args.worker_id,
        total_workers=args.total_workers,
        batch_size=args.batch_size,
        db_url=args.db_url or "",
    )
    config.validate()
    model = load_model(config.model)
    while True:
        result = process_once(config, model)
        print(result.log_line(config.worker_id), flush=True)
        if not args.loop or result.selected == 0:
            return
        if args.loop_interval > 0:
            time.sleep(args.loop_interval)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
