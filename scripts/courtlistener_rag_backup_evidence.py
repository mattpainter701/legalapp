#!/usr/bin/env python3
"""Write fail-closed evidence for a tagged CourtListener RAG Restic snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


CHECKSUM_LINE = re.compile(r"^[0-9a-f]{64}  [^\r\n]+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, nargs="+", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.snapshots.is_symlink() or not args.snapshots.is_file():
            raise ValueError("snapshot evidence must be a regular file")
        snapshots = json.loads(args.snapshots.read_text(encoding="utf-8"))
        if not isinstance(snapshots, list) or len(snapshots) != 1:
            raise ValueError("expected exactly one timestamp-tagged Restic snapshot")
        snapshot = snapshots[0]
        if not isinstance(snapshot, dict):
            raise ValueError("Restic snapshot entry is invalid")
        tags = snapshot.get("tags")
        if not isinstance(tags, list) or not {
            "courtlistener-rag-production",
            args.timestamp,
        }.issubset(tags):
            raise ValueError("Restic snapshot does not have the required tags")
        snapshot_id = snapshot.get("short_id") or snapshot.get("id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ValueError("Restic snapshot has no identifier")
        created_at = datetime.fromisoformat(
            str(snapshot["time"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        expected_at = datetime.strptime(args.timestamp, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
        if abs((created_at - expected_at).total_seconds()) > 900:
            raise ValueError("snapshot is not fresh")

        artifact_hashes: dict[str, str] = {}
        for artifact in args.artifacts:
            if artifact.is_symlink() or not artifact.is_file():
                raise ValueError(f"invalid artifact: {artifact}")
            checksum_lines = artifact.read_text(encoding="utf-8").splitlines()
            if not checksum_lines or any(
                not CHECKSUM_LINE.fullmatch(line) for line in checksum_lines
            ):
                raise ValueError(f"empty checksum: {artifact}")
            artifact_hashes[artifact.name] = hashlib.sha256(
                artifact.read_bytes()
            ).hexdigest()

        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("refusing to overwrite evidence")
        descriptor = os.open(
            args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(
                {
                    "schema_version": 1,
                    "timestamp": args.timestamp,
                    "restic_snapshot_id": snapshot_id,
                    "restic_snapshot_time": created_at.isoformat(),
                    "artifact_checksum_files": artifact_hashes,
                },
                target,
                sort_keys=True,
            )
            target.write("\n")
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"CourtListener RAG backup evidence error: {exc}", file=sys.stderr)
        return 2

    print(f"COURTLISTENER_RAG_BACKUP_EVIDENCE={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
