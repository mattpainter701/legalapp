"""
Phase 2 embedding worker for NVIDIA Jetson Orin.
Reads unembedded chunks from PostgreSQL, embeds with bge-small-en-v1.5, writes back.

Usage: python jetson_embed_worker.py --worker-id 0 --total-workers 3

This script is designed to run multiple instances in parallel across Jetson Orin
nodes, with each worker handling a disjoint subset of rows via modulo partitioning.

Requirements:
    pip install psycopg2-binary sentence-transformers tqdm python-dotenv torch

Model: BAAI/bge-small-en-v1.5
  - 384-dimensional embeddings
  - Fast and accurate for retrieval tasks
  - Optimized for CPU/ARM inference
"""

import argparse
import os
import sys
import time
from typing import List, Optional, Tuple

import psycopg2
import psycopg2.extras
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_BATCH_SIZE = 64
BGE_DIMENSIONS = 384

# Query to get unembedded chunks for this worker
FETCH_QUERY = """
    SELECT id, content
    FROM public_chunks
    WHERE embedding IS NULL
      AND (ABS(HASHTEXT(id::text)) %% %s) = %s
    LIMIT %s;
"""

UPDATE_QUERY = """
    UPDATE public_chunks
    SET embedding = %s::vector
    WHERE id = %s;
"""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_connection(db_url: str):
    """Create a psycopg2 connection."""
    url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(url, connect_timeout=30)


def count_unembedded(conn, total_workers: int, worker_id: int) -> int:
    """Return the count of unembedded chunks for this worker partition."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM public_chunks
            WHERE embedding IS NULL
              AND (ABS(HASHTEXT(id::text)) %% %s) = %s;
            """,
            (total_workers, worker_id),
        )
        row = cur.fetchone()
        return row[0] if row else 0


def fetch_batch(
    conn,
    total_workers: int,
    worker_id: int,
    batch_size: int,
) -> List[Tuple[str, str]]:
    """Fetch a batch of unembedded chunks for this worker."""
    with conn.cursor() as cur:
        cur.execute(FETCH_QUERY, (total_workers, worker_id, batch_size))
        return cur.fetchall()


def update_embeddings(conn, updates: List[Tuple[str, str]]):
    """
    Write embeddings back.
    updates: list of (embedding_str, chunk_id) tuples
    """
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            UPDATE_QUERY,
            updates,
            page_size=100,
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def load_model(model_name: str):
    """Load sentence-transformers model with Jetson-friendly settings."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("ERROR: sentence-transformers not installed.")
        print("  Install: pip install sentence-transformers")
        sys.exit(1)

    print(f"Loading model: {model_name}")
    start = time.time()

    # On Jetson Orin, use CPU or CUDA if available
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  Device: {device}")

    model = SentenceTransformer(model_name, device=device)
    elapsed = time.time() - start
    print(f"  Model loaded in {elapsed:.1f}s")
    return model


def embed_batch(model, texts: List[str], normalize: bool = True) -> List[List[float]]:
    """
    Embed a batch of texts using the sentence-transformers model.
    BGE models benefit from adding instruction prefix for retrieval.
    """
    # BGE recommends this prefix for retrieval/passage encoding
    prefixed = ["Represent this sentence for searching relevant passages: " + t for t in texts]

    embeddings = model.encode(
        prefixed,
        batch_size=len(prefixed),
        normalize_embeddings=normalize,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [emb.tolist() for emb in embeddings]


def format_embedding(emb: List[float]) -> str:
    """Format embedding list as pgvector string."""
    return "[" + ",".join(f"{v:.8f}" for v in emb) + "]"


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------

class WorkerStats:
    def __init__(self):
        self.batches_processed = 0
        self.chunks_embedded = 0
        self.errors = 0
        self.start_time = time.time()

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def rate(self) -> float:
        elapsed = self.elapsed()
        if elapsed < 0.1:
            return 0.0
        return self.chunks_embedded / elapsed

    def summary(self) -> str:
        return (
            f"Embedded: {self.chunks_embedded:,} chunks | "
            f"Rate: {self.rate():.1f} chunks/s | "
            f"Elapsed: {self.elapsed():.0f}s | "
            f"Errors: {self.errors}"
        )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_worker(
    conn,
    model,
    worker_id: int,
    total_workers: int,
    batch_size: int,
):
    stats = WorkerStats()

    print(f"\nWorker {worker_id}/{total_workers}: counting unembedded chunks...")
    total_remaining = count_unembedded(conn, total_workers, worker_id)
    print(f"  Chunks to process: {total_remaining:,}")

    if total_remaining == 0:
        print("  Nothing to do. Exiting.")
        return stats

    with tqdm(
        total=total_remaining,
        desc=f"Worker {worker_id}",
        unit="chunk",
        dynamic_ncols=True,
    ) as pbar:
        while True:
            # Fetch next batch
            rows = fetch_batch(conn, total_workers, worker_id, batch_size)
            if not rows:
                break

            ids = [row[0] for row in rows]
            texts = [row[1] for row in rows]

            # Embed
            try:
                embeddings = embed_batch(model, texts)
            except Exception as e:
                print(f"\n[ERROR] Embedding batch failed: {e}")
                stats.errors += len(texts)
                pbar.update(len(texts))
                continue

            # Prepare updates
            updates = []
            for chunk_id, emb in zip(ids, embeddings):
                if emb is None or len(emb) == 0:
                    stats.errors += 1
                    continue
                emb_str = format_embedding(emb)
                updates.append((emb_str, chunk_id))

            # Write to DB
            if updates:
                try:
                    update_embeddings(conn, updates)
                    stats.chunks_embedded += len(updates)
                    stats.batches_processed += 1
                except Exception as e:
                    print(f"\n[ERROR] DB update failed: {e}")
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    stats.errors += len(updates)

            pbar.update(len(rows))
            pbar.set_postfix_str(
                f"{stats.rate():.1f} c/s | errors: {stats.errors}"
            )

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Jetson Orin embedding worker for Clarity Legal"
    )
    parser.add_argument(
        "--worker-id",
        type=int,
        required=True,
        help="This worker's index (0-based)"
    )
    parser.add_argument(
        "--total-workers",
        type=int,
        required=True,
        help="Total number of parallel workers"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Chunks per batch (default: {DEFAULT_BATCH_SIZE})"
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="PostgreSQL connection URL (defaults to DATABASE_URL env var)"
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Sentence-transformers model name (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running in a loop until no unembedded chunks remain"
    )
    parser.add_argument(
        "--loop-interval",
        type=int,
        default=60,
        help="Seconds to wait between loop iterations (default: 60)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.worker_id < 0 or args.worker_id >= args.total_workers:
        print(f"ERROR: --worker-id must be 0..{args.total_workers - 1}")
        sys.exit(1)

    db_url = args.db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: --db-url or DATABASE_URL env var required.")
        sys.exit(1)

    print(f"=== Clarity Legal Jetson Embed Worker ===")
    print(f"  Worker: {args.worker_id} of {args.total_workers}")
    print(f"  Model:  {args.model}")
    print(f"  Batch:  {args.batch_size}")
    print(f"  DB:     {db_url[:40]}...")

    # Load model once
    model = load_model(args.model)

    while True:
        print(f"\nConnecting to database...")
        conn = get_connection(db_url)

        try:
            stats = run_worker(
                conn=conn,
                model=model,
                worker_id=args.worker_id,
                total_workers=args.total_workers,
                batch_size=args.batch_size,
            )
        finally:
            conn.close()

        print(f"\n{stats.summary()}")

        if not args.loop:
            break

        print(f"Sleeping {args.loop_interval}s before next pass...")
        time.sleep(args.loop_interval)

    print("Worker finished.")


if __name__ == "__main__":
    main()
