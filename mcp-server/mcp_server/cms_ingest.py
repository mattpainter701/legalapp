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

DISCOVERY_SOURCE_KEYS = {
    "manual": "cms:internet-only-manuals",
    "transmittal": "cms:transmittals",
}


def _write_checkpoint(
    directory: Path,
    partition: str,
    completed: int,
    status: str = "running",
    failures: list[dict] | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"cms-{partition}.json").write_text(
        json.dumps(
            {
                "completed": completed,
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "failures": failures or [],
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
        if state.get("status") not in {"running", "partial_failure"}:
            return 0
        return max(0, int(state.get("completed", 0)))
    except (OSError, ValueError):
        return 0


def _failure(
    source_key: str,
    stage: str,
    exc: Exception,
    *,
    external_id: str | None = None,
    canonical_url: str | None = None,
) -> dict:
    failure = {
        "source_key": source_key,
        "stage": stage,
        "error": str(exc)[-1000:],
    }
    if external_id:
        failure["external_id"] = external_id
    if canonical_url:
        failure["canonical_url"] = canonical_url
    return failure


def _fetch_discovered_documents(
    entries: list,
    *,
    client: httpx.Client,
) -> tuple[list, list[dict]]:
    """Fetch every artifact independently so one malformed PDF cannot stop a source."""
    documents = []
    failures: list[dict] = []
    for entry in entries:
        try:
            documents.append(fetch_manifest_document(entry, client=client))
        except Exception as exc:
            failures.append(
                _failure(
                    entry.source_key,
                    "artifact_fetch",
                    exc,
                    external_id=entry.external_id,
                    canonical_url=entry.canonical_url,
                )
            )
    return documents, failures


def _record_source_failures(conn: object, failures: list[dict]) -> None:
    failures_by_source: dict[str, list[dict]] = {}
    for failure in failures:
        failures_by_source.setdefault(failure["source_key"], []).append(failure)
    with conn.cursor() as cursor:  # type: ignore[attr-defined]
        for source_key, source_failures in sorted(failures_by_source.items()):
            summary = "; ".join(
                f"{failure['stage']}"
                + (f" {failure['external_id']}" if failure.get("external_id") else "")
                + f": {failure['error']}"
                for failure in source_failures
            )[-2000:]
            cursor.execute(
                """UPDATE legal_sources
                   SET last_attempted_at=now(), current_error=%s,
                       item_count=(SELECT COUNT(*) FROM legal_documents WHERE source_key=%s),
                       chunk_count=(SELECT COUNT(*) FROM legal_document_chunks c
                                    JOIN legal_documents d ON d.id=c.document_id
                                    WHERE d.source_key=%s),
                       embedded_chunk_count=(SELECT COUNT(*) FROM legal_document_chunks c
                                             JOIN legal_documents d ON d.id=c.document_id
                                             WHERE d.source_key=%s AND c.embedding IS NOT NULL),
                       updated_at=now()
                   WHERE source_key=%s""",
                [summary, source_key, source_key, source_key, source_key],
            )


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
    failures: list[dict] = []
    requested_sources: set[str] = set()
    coverage_progress: dict[str, int] = {}
    discovery_progress: dict[str, int] = {}
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
            source_key = DISCOVERY_SOURCE_KEYS[kind]
            requested_sources.add(source_key)
            try:
                entries = discover_cms_artifacts(
                    client, page_url=page, kind=kind, limit=args.limit
                )
            except Exception as exc:
                failures.append(_failure(source_key, "discovery", exc, canonical_url=page))
                discovery_progress[kind] = 0
                continue
            discovery_progress[kind] = len(entries)
            if args.preview:
                output.extend({"discovery": kind, "external_id": entry.external_id, "title": entry.title, "canonical_url": entry.canonical_url} for entry in entries)
            else:
                fetched, artifact_failures = _fetch_discovered_documents(
                    entries, client=client
                )
                discovered_documents.extend(fetched)
                failures.extend(artifact_failures)
        documents = []
        for entity in args.entities or []:
            source_key = "cms:medicare-coverage-api"
            requested_sources.add(source_key)
            completed = _checkpoint_completed(checkpoints, entity) if args.sync else 0
            fetch_limit = None if args.limit is None else args.limit + completed
            processed = completed
            try:
                for index, item in enumerate(
                    paged_coverage_items(client, entity, limit=fetch_limit)
                ):
                    if index < completed:
                        continue
                    processed = index + 1
                    try:
                        documents.append(
                            coverage_document(
                                entity, item, retrieved_at=datetime.now(timezone.utc)
                            )
                        )
                    except Exception as exc:
                        failures.append(_failure(source_key, f"coverage_{entity}", exc))
            except Exception as exc:
                failures.append(_failure(source_key, f"coverage_{entity}", exc))
            coverage_progress[entity] = processed
    if args.sync:
        with connect(args.db_url) as conn:
            for document in documents + discovered_documents:
                output.append(upsert_adapter_document(conn, document))
            failed_sources = {failure["source_key"] for failure in failures}
            refresh_source_status(conn, requested_sources - failed_sources)
            _record_source_failures(conn, failures)
            conn.commit()
        for entity, completed in coverage_progress.items():
            entity_failures = [
                failure
                for failure in failures
                if failure["source_key"] == "cms:medicare-coverage-api"
                and failure["stage"] == f"coverage_{entity}"
            ]
            _write_checkpoint(
                checkpoints,
                entity,
                completed,
                status="partial_failure" if entity_failures else "complete",
                failures=entity_failures,
            )
        for kind, completed in discovery_progress.items():
            source_key = DISCOVERY_SOURCE_KEYS[kind]
            source_failures = [
                failure for failure in failures if failure["source_key"] == source_key
            ]
            _write_checkpoint(
                checkpoints,
                source_key.replace(":", "-"),
                completed,
                status="partial_failure" if source_failures else "complete",
                failures=source_failures,
            )
    else:
        output.extend({"external_id": doc.external_id, "title": doc.title, "dry_run": True} for doc in documents)
    report = {
        "status": "partial_failure" if failures else "succeeded",
        "document_count": len(output),
        "failure_count": len(failures),
        "documents": output,
        "failures": failures,
    }
    # Isolated source/artifact failures are represented in current_error and the
    # structured report. Returning normally keeps the scheduler from replacing
    # that source-specific state with the same blanket error on every CMS source.
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
