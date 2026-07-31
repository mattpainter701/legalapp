from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from typing import Callable

from .database import connect
from .dispatcher import dispatch_targets, jetson_target_specs_from_env

DEFAULT_LOCK_ID = 2026062901


@dataclass(frozen=True)
class SchedulerConfig:
    db_url: str
    worker_db_url: str | None = None
    hosts: str = ""
    user: str = "jetson"
    script_dir: str = "/home/jetson/legalapp/scripts"
    batch_size: int = 32
    interval_seconds: int = 900
    minimum_unembedded: int = 1
    reverse_tunnel: bool = False
    tunnel_remote_port_base: int = 15434
    advisory_lock_id: int = DEFAULT_LOCK_ID


@dataclass(frozen=True)
class SchedulerResult:
    dispatched: bool
    reason: str
    unembedded_count: int = 0


DispatchFn = Callable[..., None]


def acquire_lock(conn, lock_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        row = cur.fetchone()
        return bool(row and row[0])


def release_lock(conn, lock_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
    conn.commit()


def unembedded_chunk_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM opinion_chunks WHERE embedding IS NULL)
              + (SELECT COUNT(*) FROM legal_document_chunks WHERE embedding IS NULL)
            """
        )
        row = cur.fetchone()
        return int(row[0] if row else 0)


def run_scheduler_once(
    conn,
    config: SchedulerConfig,
    *,
    dispatch: DispatchFn = dispatch_targets,
) -> SchedulerResult:
    if not acquire_lock(conn, config.advisory_lock_id):
        return SchedulerResult(dispatched=False, reason="lock_held")

    try:
        unembedded = unembedded_chunk_count(conn)
        if unembedded < config.minimum_unembedded:
            return SchedulerResult(
                dispatched=False,
                reason="below_threshold",
                unembedded_count=unembedded,
            )

        targets = jetson_target_specs_from_env(config.hosts, config.user)
        dispatch(
            targets,
            config.script_dir,
            config.worker_db_url or config.db_url,
            config.batch_size,
            reverse_tunnel=config.reverse_tunnel,
            tunnel_remote_port_base=config.tunnel_remote_port_base,
        )
        return SchedulerResult(
            dispatched=True,
            reason="dispatched",
            unembedded_count=unembedded,
        )
    finally:
        release_lock(conn, config.advisory_lock_id)


def config_from_env(args: argparse.Namespace) -> SchedulerConfig:
    db_url = args.db_url or os.environ.get("SCHEDULER_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("SCHEDULER_DB_URL, DATABASE_URL, or --db-url is required")
    worker_db_url = (
        args.worker_db_url
        or os.environ.get("EMBEDDING_WORKER_DB_URL")
        or os.environ.get("VECTORDB_URL")
        or db_url
    )
    return SchedulerConfig(
        db_url=db_url,
        worker_db_url=worker_db_url,
        hosts=args.hosts,
        user=args.user,
        script_dir=args.script_dir,
        batch_size=args.batch_size,
        interval_seconds=args.interval_seconds,
        minimum_unembedded=args.minimum_unembedded,
        reverse_tunnel=args.reverse_tunnel,
        tunnel_remote_port_base=args.tunnel_remote_port_base,
        advisory_lock_id=args.advisory_lock_id,
    )


def run_scheduler(config: SchedulerConfig) -> None:
    while True:
        try:
            with connect(config.db_url) as conn:
                result = run_scheduler_once(conn, config)
            print(
                "embedding_scheduler "
                f"reason={result.reason} "
                f"dispatched={result.dispatched} "
                f"unembedded={result.unembedded_count}",
                flush=True,
            )
        except Exception as exc:
            print(f"embedding_scheduler error={exc}", flush=True)
        time.sleep(config.interval_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule CourtListener Jetson embeddings")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--worker-db-url", default="")
    parser.add_argument("--hosts", default=os.environ.get("JETSON_HOSTS", ""))
    parser.add_argument("--user", default=os.environ.get("JETSON_USER", "jetson"))
    parser.add_argument(
        "--script-dir",
        default=os.environ.get("JETSON_SCRIPT_DIR", "/home/jetson/legalapp/scripts"),
    )
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("BATCH_SIZE", "32")))
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.environ.get("EMBEDDING_SCHEDULER_INTERVAL_SECONDS", "900")),
    )
    parser.add_argument(
        "--minimum-unembedded",
        type=int,
        default=int(os.environ.get("EMBEDDING_SCHEDULER_MINIMUM_UNEMBEDDED", "1")),
    )
    parser.add_argument(
        "--reverse-tunnel",
        action="store_true",
        default=os.environ.get("JETSON_DB_REVERSE_TUNNEL", "").lower() in {"1", "true", "yes"},
    )
    parser.add_argument(
        "--tunnel-remote-port-base",
        type=int,
        default=int(os.environ.get("JETSON_DB_TUNNEL_REMOTE_PORT_BASE", "15434")),
    )
    parser.add_argument(
        "--advisory-lock-id",
        type=int,
        default=int(os.environ.get("EMBEDDING_SCHEDULER_LOCK_ID", str(DEFAULT_LOCK_ID))),
    )
    return parser.parse_args()


def main() -> None:
    run_scheduler(config_from_env(parse_args()))


if __name__ == "__main__":
    main()
