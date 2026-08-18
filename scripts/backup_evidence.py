#!/usr/bin/env python3
"""Write small, machine-readable proof of a fresh Restic production backup."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path


class EvidenceError(RuntimeError):
    pass


def _snapshot_time(value: object) -> int:
    if not isinstance(value, str):
        raise EvidenceError("Restic snapshot has no timestamp")
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError as exc:
        raise EvidenceError("Restic snapshot timestamp is invalid") from exc


def create(args: argparse.Namespace) -> None:
    snapshots = json.loads(args.snapshots.read_text(encoding="utf-8"))
    if not isinstance(snapshots, list):
        raise EvidenceError("Restic snapshots output is not a list")
    matching = [
        item
        for item in snapshots
        if isinstance(item, dict)
        and args.timestamp in item.get("tags", [])
        and "legalapp-production" in item.get("tags", [])
    ]
    if len(matching) != 1:
        raise EvidenceError(
            "expected exactly one tagged Restic snapshot for this backup"
        )
    snapshot = matching[0]
    snapshot_id = snapshot.get("short_id") or snapshot.get("id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise EvidenceError("Restic snapshot has no identifier")
    created_at = _snapshot_time(snapshot.get("time"))
    now = int(time.time())
    if created_at > now + 60 or now - created_at > args.max_age_seconds:
        raise EvidenceError(
            "Restic snapshot is not fresh enough for a production release"
        )
    if args.courtlistener_classification not in {
        "rebuildable-public-corpus",
        "separately-protected",
    }:
        raise EvidenceError("CourtListener RAG classification is invalid")
    payload = {
        "schema_version": 1,
        "created_at": now,
        "backup_timestamp": args.timestamp,
        "restic_snapshot_id": snapshot_id,
        "restic_snapshot_time": created_at,
        "off_host_encrypted": True,
        "rag_persistence": {
            "private_legalapp_vectors_and_documents": {
                "locations": ["LegalApp PostgreSQL dump", "immutable uploads artifact"],
                "covered_by_snapshot": True,
            },
            "courtlistener_corpus": {
                "locations": [
                    "courtlistener_pgdata Docker volume",
                    "courtlistener_bulk Docker volume",
                    "legal_authority_cache Docker volume",
                ],
                "classification": args.courtlistener_classification,
                "covered_by_snapshot": False,
            },
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(payload, target, sort_keys=True)
        target.write("\n")
    if args.status_output is not None:
        args.status_output.parent.mkdir(parents=True, exist_ok=True)
        if args.status_output.is_symlink():
            raise EvidenceError("backup status path must not be a symlink")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".backup-status.", dir=args.status_output.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(
                    {
                        "schema_version": 1,
                        "completed_at_epoch": now,
                        "status": "ok",
                        "offsite": True,
                        "components": [
                            "legalapp_database",
                            "litellm_database",
                            "uploads",
                            "key_escrow",
                        ],
                    },
                    target,
                    sort_keys=True,
                )
                target.write("\n")
            os.replace(temporary, args.status_output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print(f"OFFSITE_BACKUP_EVIDENCE={args.output}")
    print(f"OFFSITE_BACKUP_SNAPSHOT={snapshot_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status-output", type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=900)
    parser.add_argument(
        "--courtlistener-classification", default="rebuildable-public-corpus"
    )
    args = parser.parse_args()
    if not args.timestamp or args.max_age_seconds < 60:
        raise EvidenceError(
            "timestamp and a max age of at least 60 seconds are required"
        )
    if args.snapshots.is_symlink() or not args.snapshots.is_file():
        raise EvidenceError("Restic snapshot evidence must be a regular file")
    if args.output.exists():
        raise EvidenceError("refusing to overwrite backup evidence")
    create(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvidenceError, OSError, json.JSONDecodeError) as exc:
        print(f"backup evidence error: {exc}", file=__import__("sys").stderr)
        raise SystemExit(2)
