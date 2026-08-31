"""Direct CUDA mxbai worker for the operator workstation.

The database selection, row locking, sharding, and write semantics remain in
``mcp_server.jetson_worker``. This adapter changes only model inference so a
Windows CUDA workstation can use the published mixedbread Transformer directly.
"""

from __future__ import annotations

import argparse
import os
import time

import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

from mcp_server.jetson_worker import DEFAULT_MODEL, WorkerConfig, process_once
from mcp_server.opinion_backfill import (
    OpinionBackfillConfig,
    process_once as process_opinion_backfill_once,
    require_queue_index,
)


MODEL_ID = "mixedbread-ai/mxbai-embed-large-v1"


class DirectCudaMxbai:
    def __init__(self) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for the direct workstation worker")
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        self.model = AutoModel.from_pretrained(MODEL_ID, dtype=torch.float16)
        self.model = self.model.to("cuda").eval()

    def encode(self, texts: list[str], **_: object):
        tokens = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        tokens = {
            key: value.to("cuda", non_blocking=True) for key, value in tokens.items()
        }
        with torch.inference_mode():
            hidden = self.model(**tokens).last_hidden_state
            vectors = F.normalize(hidden[:, 0], p=2, dim=1)
        return vectors.float().cpu().numpy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LawHand direct CUDA legal-authority embedding worker"
    )
    parser.add_argument("--worker-id", type=int, default=0)
    parser.add_argument("--total-workers", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument(
        "--db-url",
        default=os.environ.get("VECTORDB_URL") or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--loop-interval", type=float, default=0.0)
    parser.add_argument(
        "--opinion-stage",
        action="store_true",
        help="Write legacy opinion vectors to the durable staging table",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.db_url:
        raise SystemExit("--db-url, VECTORDB_URL, or DATABASE_URL is required")
    config = (
        OpinionBackfillConfig(
            worker_id=args.worker_id,
            total_workers=args.total_workers,
            batch_size=args.batch_size,
            db_url=args.db_url,
        )
        if args.opinion_stage
        else WorkerConfig(
            worker_id=args.worker_id,
            total_workers=args.total_workers,
            batch_size=args.batch_size,
            model=DEFAULT_MODEL,
            dim=1024,
            db_url=args.db_url,
        )
    )
    config.validate()
    if args.opinion_stage:
        require_queue_index(config.db_url)
    model = DirectCudaMxbai()
    cursor_created_at = None
    cursor_chunk_id = None
    while True:
        result = (
            process_opinion_backfill_once(
                config,
                model,
                cursor_created_at=cursor_created_at,
                cursor_chunk_id=cursor_chunk_id,
            )
            if args.opinion_stage
            else process_once(config, model)
        )
        count = result.staged if args.opinion_stage else result
        line = (
            result.log_line(config.worker_id)
            if args.opinion_stage
            else f"direct_cuda worker={config.worker_id} embedded={count}"
        )
        print(line, flush=True)
        if not args.loop:
            return
        if args.opinion_stage:
            if result.selected == 0:
                if cursor_created_at is not None:
                    cursor_created_at = None
                    cursor_chunk_id = None
                    continue
                return
            cursor_created_at = result.cursor_created_at
            cursor_chunk_id = result.cursor_chunk_id
        elif count == 0:
            return
        if args.loop_interval > 0:
            time.sleep(args.loop_interval)


if __name__ == "__main__":
    main()
