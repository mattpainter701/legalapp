from __future__ import annotations

import argparse
import os
import sys
import time
import json
import platform
import shutil
import threading
from collections import Counter
from typing import Iterable

import httpx
import psycopg2.extras

from .database import connect
from .control_plane import claim_embedding_shard, finish_embedding_shard, heartbeat_embedding_shard
from .worker_config import DEFAULT_MODEL, WorkerConfig, partition_sql

# The reviewed authority corpus is small and high-value for chat. Drain it before
# returning to the much larger CourtListener opinion backlog.
CORPORA = ("legal_document_chunks", "authority_case_chunks")


def measured_host_telemetry() -> dict:
    """Return measured worker health, with explicit unavailable values."""
    telemetry = {
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": platform.node() or "unknown",
        "platform": platform.platform(),
        "temperature_c": None,
        "temperature_source": "unavailable",
        "capacity": {"disk_free_bytes": None, "cpu_count": os.cpu_count()},
        "capacity_source": "os",
    }
    try:
        usage = shutil.disk_usage(os.getcwd())
        telemetry["capacity"]["disk_free_bytes"] = usage.free
    except OSError:
        telemetry["capacity_source"] = "unavailable"
    try:
        import psutil

        temperatures = psutil.sensors_temperatures(fahrenheit=False)
        readings = [
            entry.current
            for entries in temperatures.values()
            for entry in entries
            if entry.current is not None
        ]
        if readings:
            telemetry["temperature_c"] = max(readings)
            telemetry["temperature_source"] = "psutil"
        memory = psutil.virtual_memory()
        telemetry["capacity"].update({
            "memory_available_bytes": memory.available,
            "memory_total_bytes": memory.total,
        })
        telemetry["capacity_source"] = "psutil"
    except (ImportError, OSError, RuntimeError):
        pass
    max_temperature = float(os.getenv("AUTHORITY_MAX_TEMPERATURE_C", "85"))
    min_disk_free = int(os.getenv("AUTHORITY_MIN_DISK_FREE_BYTES", "0"))
    if telemetry["temperature_c"] is None or telemetry["capacity"]["disk_free_bytes"] is None:
        health_state = "unavailable"
        health_reason = "temperature or disk capacity telemetry unavailable"
    elif telemetry["temperature_c"] > max_temperature:
        health_state = "threshold_exceeded"
        health_reason = "measured temperature exceeds configured threshold"
    elif telemetry["capacity"]["disk_free_bytes"] < min_disk_free:
        health_state = "threshold_exceeded"
        health_reason = "measured free disk is below configured threshold"
    else:
        health_state = "healthy"
        health_reason = None
    telemetry["health"] = {
        "state": health_state,
        "reason": health_reason,
        "thresholds": {
            "max_temperature_c": max_temperature,
            "min_disk_free_bytes": min_disk_free,
        },
        "sample_age_seconds": 0,
    }
    return telemetry


def update_sql(corpus: str, model_version: str = "1") -> str:
    if corpus not in CORPORA:
        raise ValueError(f"unsupported embedding corpus: {corpus}")
    updated_at = ",\n            updated_at = now()" if corpus == "legal_document_chunks" else ""
    identity_column = "id" if corpus == "legal_document_chunks" else "chunk_id"
    return f"""
        UPDATE {corpus}
        SET embedding = %s::vector,
            embedding_model = %s,
            embedding_version = %s{updated_at}
        WHERE {identity_column} = %s
    """


def format_embedding(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


class OllamaEmbeddingModel:
    """Small SentenceTransformer-compatible adapter for workstation failover."""

    def __init__(self, base_url: str, model_name: str, timeout_seconds: float = 120.0):
        self.model_name = model_name
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

    def encode(self, texts: list[str], **_: object) -> list[list[float]]:
        response = self.client.post(
            "/api/embed",
            json={"model": self.model_name, "input": texts, "keep_alive": "30m"},
        )
        response.raise_for_status()
        vectors = response.json().get("embeddings", [])
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Ollama returned {len(vectors)} embeddings for {len(texts)} inputs"
            )
        if any(len(vector) != 1024 for vector in vectors):
            raise RuntimeError("Ollama mxbai embeddings must be 1024-dimensional")
        return vectors


def load_model(
    model_name: str,
    *,
    ollama_url: str = "",
    ollama_model: str = "mxbai-embed-large",
):
    if ollama_url:
        return OllamaEmbeddingModel(ollama_url, ollama_model)
    try:
        from sentence_transformers import SentenceTransformer
        import torch
    except ImportError as exc:
        raise RuntimeError("sentence-transformers and torch are required on Jetson workers") from exc
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return SentenceTransformer(model_name, device=device)


def embed_batch(model, texts: list[str], batch_size: int) -> list[list[float]]:
    prefixed = ["Represent this sentence for searching relevant passages: " + text for text in texts]
    vectors = model.encode(
        prefixed,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [vector.tolist() if hasattr(vector, "tolist") else list(vector) for vector in vectors]


class _LeaseHeartbeat:
    """Renew a shard lease on an independent connection during inference."""

    def __init__(self, db_url, shard_key: str, worker_id: str, interval: float = 30.0):
        self.db_url = db_url
        self.shard_key = shard_key
        self.worker_id = worker_id
        self.interval = max(float(interval), 0.01)
        self.lost = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                with connect(self.db_url) as heartbeat_conn:
                    if not heartbeat_embedding_shard(
                        heartbeat_conn,
                        shard_key=self.shard_key,
                        worker_id=self.worker_id,
                        lease_seconds=900,
                    ):
                        self.lost.set()
                        return
            except Exception:
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.stop.set()
        self.thread.join(timeout=max(self.interval * 2, 1.0))


def process_once(config: WorkerConfig, model) -> int:
    config.validate()
    overall_total = 0
    telemetry = measured_host_telemetry()
    with connect(config.db_url) as conn:
        for corpus in CORPORA:
            with conn.cursor() as cur:
                requested = os.getenv("AUTHORITY_EMBEDDING_CORPUS_VERSION", "")
                cur.execute("SELECT version, embedding_model, embedding_version, embedding_dimension FROM authority_corpus_versions WHERE status IN ('promoted','staged','canary') AND (%s = '' OR version=%s) ORDER BY CASE WHEN version=%s THEN 0 WHEN status='staged' THEN 1 WHEN status='canary' THEN 2 ELSE 3 END, promoted_at DESC NULLS LAST LIMIT 1", [requested, requested, requested])
                version_row = cur.fetchone()
                if not version_row:
                    continue
                version, corpus_model, corpus_model_version, corpus_dimension = version_row
                if (corpus_model, str(corpus_model_version), corpus_dimension) != (
                    config.model,
                    str(config.model_version),
                    config.dim,
                ):
                    raise RuntimeError(
                        f"embedding contract mismatch for {version}: "
                        f"corpus={(corpus_model, corpus_model_version, corpus_dimension)!r} "
                        f"worker={(config.model, config.model_version, config.dim)!r}"
                    )
                shard_key = f"{corpus}:{config.worker_id}:{config.total_workers}:{version}"
                cur.execute("""INSERT INTO authority_embedding_shards
                    (shard_key, corpus_version, corpus_table, model, model_version, dimension,
                     temperature_c, capacity_evidence)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT DO NOTHING""",
                    [shard_key, version, corpus, config.model, config.model_version, config.dim,
                     telemetry["temperature_c"], json.dumps(telemetry)])
                cur.execute(
                    f"""UPDATE authority_embedding_shards SET status='queued',
                           lease_owner=NULL, lease_expires_at=NULL, heartbeat_at=NULL,
                           attempts=0, last_error=NULL, dead_letter_reason=NULL,
                           updated_at=now()
                        WHERE shard_key=%s AND status='complete'
                          AND EXISTS (SELECT 1 FROM {corpus}
                                      WHERE (embedding IS NULL
                                         OR embedding_model IS DISTINCT FROM %s
                                         OR embedding_version::text IS DISTINCT FROM %s
                                         OR vector_dims(embedding) IS DISTINCT FROM %s)
                                        AND corpus_version=%s)""",
                    [shard_key, config.model, config.model_version, config.dim, version],
                )
            conn.commit()
            if not claim_embedding_shard(conn, shard_key=shard_key, worker_id=str(config.worker_id)):
                continue
            if not heartbeat_embedding_shard(conn, shard_key=shard_key, worker_id=str(config.worker_id), lease_seconds=900):
                finish_embedding_shard(conn, shard_key=shard_key, worker_id=str(config.worker_id), success=False, error="embedding lease lost before processing")
                continue
            started = time.monotonic()
            total = 0
            try:
                with _LeaseHeartbeat(
                    config.db_url,
                    shard_key,
                    str(config.worker_id),
                    os.getenv("AUTHORITY_HEARTBEAT_INTERVAL_SECONDS", "30"),
                ) as lease:
                  while True:
                    if not heartbeat_embedding_shard(conn, shard_key=shard_key, worker_id=str(config.worker_id), lease_seconds=900):
                        raise RuntimeError("embedding lease lost before batch")
                    with conn.cursor() as cur:
                        cur.execute("BEGIN")
                        contract_params = [config.model, config.model_version, config.dim]
                        version_params = (contract_params + [version] if corpus == "authority_case_chunks"
                                          else contract_params + [version, version] if corpus == "legal_document_chunks" else [])
                        cur.execute(
                            partition_sql(corpus, version, config.model, config.model_version, config.dim),
                            version_params + [config.total_workers, config.worker_id, config.batch_size],
                        )
                        rows = cur.fetchall()
                        if not rows:
                            conn.rollback()
                            break
                        ids = [row[0] for row in rows]
                        texts = [row[1] for row in rows]
                        vectors = embed_batch(model, texts, config.batch_size)
                        if len(vectors) != len(ids) or any(len(vector) != config.dim for vector in vectors):
                            raise RuntimeError("embedding output count or dimension mismatch")
                        if lease.lost.is_set():
                            raise RuntimeError("embedding lease lost during inference")
                        with connect(config.db_url) as lease_conn:
                            renewed = heartbeat_embedding_shard(
                                lease_conn,
                                shard_key=shard_key,
                                worker_id=str(config.worker_id),
                                lease_seconds=900,
                            )
                        if not renewed or lease.lost.is_set():
                            raise RuntimeError("embedding lease lost before writes")
                        updates = [(format_embedding(vector), config.model, config.model_version, chunk_id) for chunk_id, vector in zip(ids, vectors)]
                        psycopg2.extras.execute_batch(cur, update_sql(corpus), updates, page_size=100)
                        if corpus == "legal_document_chunks":
                            for source_key, embedded_count in Counter(row[2] for row in rows).items():
                                cur.execute(
                                    """UPDATE legal_sources
                                       SET embedded_chunk_count = embedded_chunk_count + %s,
                                           updated_at = now()
                                       WHERE source_key = %s""",
                                    [embedded_count, source_key],
                                )
                        conn.commit()
                        total += len(updates)
                        elapsed_batch = max(time.monotonic() - started, 0.001)
                        with conn.cursor() as cur:
                            cur.execute(
                                """UPDATE authority_embedding_shards
                                   SET throughput_per_minute=%s, temperature_c=%s,
                                       capacity_evidence=%s::jsonb, updated_at=now()
                                 WHERE shard_key=%s AND status='leased'
                                   AND lease_owner=%s""",
                                [
                                    total / elapsed_batch * 60,
                                    telemetry["temperature_c"],
                                    json.dumps({**telemetry, "batch_observed_at": time.time()}),
                                    shard_key,
                                    str(config.worker_id),
                                ],
                            )
                        conn.commit()
                elapsed = max(time.monotonic() - started, 0.001)
                finish_embedding_shard(conn, shard_key=shard_key, worker_id=str(config.worker_id), success=True, throughput_per_minute=total / elapsed * 60)
                overall_total += total
            except Exception as exc:
                conn.rollback()
                finish_embedding_shard(conn, shard_key=shard_key, worker_id=str(config.worker_id), success=False, error=str(exc), throughput_per_minute=total / max(time.monotonic() - started, 0.001) * 60)
                continue
        return overall_total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Public legal-authority mxbai embedding worker")
    parser.add_argument("--worker-id", type=int, required=True)
    parser.add_argument("--total-workers", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--db-url", default=os.environ.get("VECTORDB_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--model", default="mxbai")
    parser.add_argument("--model-version", default=os.environ.get("EMBEDDING_MODEL_VERSION", "1"))
    parser.add_argument("--dim", type=int, default=1024)
    parser.add_argument("--temperature-c", type=float, default=None)
    parser.add_argument("--capacity-evidence", default="{}")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_EMBEDDING_URL", ""))
    parser.add_argument(
        "--ollama-model",
        default=os.environ.get("OLLAMA_EMBEDDING_MODEL", "mxbai-embed-large"),
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument(
        "--loop-interval",
        type=int,
        default=0,
        help="Optional delay between successful batches; zero drains continuously",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_name = DEFAULT_MODEL if args.model == "mxbai" else args.model
    config = WorkerConfig(
        worker_id=args.worker_id,
        total_workers=args.total_workers,
        batch_size=args.batch_size,
        model=model_name,
        model_version=args.model_version,
        dim=args.dim,
        db_url=args.db_url,
        temperature_c=args.temperature_c,
        capacity_evidence=json.loads(args.capacity_evidence),
    )
    if not config.db_url:
        raise SystemExit("--db-url, VECTORDB_URL, or DATABASE_URL is required")
    config.validate()
    model = load_model(
        config.model,
        ollama_url=args.ollama_url,
        ollama_model=args.ollama_model,
    )
    while True:
        count = process_once(config, model)
        print(f"worker={config.worker_id} embedded={count}", flush=True)
        if not args.loop or count == 0:
            break
        if args.loop_interval > 0:
            time.sleep(args.loop_interval)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
