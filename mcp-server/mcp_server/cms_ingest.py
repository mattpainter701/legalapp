"""Scheduler-facing, no-token CMS coverage and discovery CLI."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .authority_adapter_store import refresh_source_status, upsert_adapter_document
from .cms_adapter import (MANUALS_URL, TRANSMITTALS_URL, USER_AGENT, coverage_document,
                          discover_cms_artifacts, fetch_manifest_document, paged_coverage_items)
from .database import connect
from .loader import init_schema
from .source_catalog import load_catalog, seed_catalog


def _write_checkpoint(
    directory: Path, partition: str, completed: int, status: str = "running"
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"cms-{partition}.json").write_text(
        json.dumps(
            {
                "completed": completed,
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )


def _checkpoint_completed(directory: Path, partition: str) -> int:
    try:
        state = json.loads(
            (directory / f"cms-{partition}.json").read_text(encoding="utf-8")
        )
        # A completed prior run must start from the first record so revisions to
        # stable CMS IDs are detected. Only an interrupted run resumes by count.
        if state.get("status") != "running":
            return 0
        return max(0, int(state.get("completed", 0)))
    except (OSError, ValueError):
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or sync public CMS coverage records/discovery manifests")
    parser.add_argument("--coverage-entity", choices=("ncd", "lcd", "article"), action="append", dest="entities")
    parser.add_argument("--discover", choices=("manual", "transmittal"), action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--checkpoint-dir", default=".legalapp-checkpoints")
    parser.add_argument("--db-url")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    if not args.entities and not args.discover:
        parser.error("choose at least one --coverage-entity or --discover")
    output: list[dict] = []
    checkpoints = Path(args.checkpoint_dir)
    if args.sync:
        init_schema(args.db_url)
        with connect(args.db_url) as conn:
            seed_catalog(conn, load_catalog())
            conn.commit()
    with httpx.Client(timeout=90, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html"}, follow_redirects=True) as client:
        discovered_documents = []
        for kind in args.discover or []:
            page = MANUALS_URL if kind == "manual" else TRANSMITTALS_URL
            entries = discover_cms_artifacts(
                client, page_url=page, kind=kind, limit=args.limit
            )
            if args.preview:
                output.extend({"discovery": kind, "external_id": entry.external_id, "title": entry.title, "canonical_url": entry.canonical_url} for entry in entries)
            else:
                discovered_documents.extend(fetch_manifest_document(entry, client=client) for entry in entries)
        documents = []
        for entity in args.entities or []:
            completed = _checkpoint_completed(checkpoints, entity) if args.sync else 0
            fetch_limit = None if args.limit is None else args.limit + completed
            for index, item in enumerate(paged_coverage_items(client, entity, limit=fetch_limit)):
                if index >= completed:
                    documents.append(coverage_document(entity, item, retrieved_at=datetime.now(timezone.utc)))
    if args.sync:
        with connect(args.db_url) as conn:
            completed_by_entity = {entity: _checkpoint_completed(checkpoints, entity) for entity in args.entities or []}
            for document in documents + discovered_documents:
                output.append(upsert_adapter_document(conn, document))
                entity = document.metadata.get("entity", document.source_key.replace(":", "-"))
                completed_by_entity[entity] = completed_by_entity.get(entity, 0) + 1
                _write_checkpoint(checkpoints, entity, completed_by_entity[entity])
            for entity in args.entities or []:
                _write_checkpoint(
                    checkpoints,
                    entity,
                    completed_by_entity[entity],
                    status="complete",
                )
            refresh_source_status(
                conn, {document.source_key for document in documents + discovered_documents}
            )
            conn.commit()
    else:
        output.extend({"external_id": doc.external_id, "title": doc.title, "dry_run": True} for doc in documents)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
