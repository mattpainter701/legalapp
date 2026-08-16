"""Scheduler-facing eCFR CLI.  See ``python -m mcp_server.ecfr_ingest --help``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from .authority_adapter_store import refresh_source_status
from .ecfr_adapter import DEFAULT_TITLES, USER_AGENT, VERSION_URL, fetch_xml, latest_snapshot, request_json, sync_title
from .loader import init_schema
from .source_catalog import load_catalog, seed_catalog
from .database import connect


def run_partitions(
    client: httpx.Client,
    titles: list[int] | tuple[int, ...],
    *,
    checkpoint_dir: Path,
    limit: int | None,
    dry_run: bool,
    db_url: str | None,
) -> dict:
    """Run each title as an independent, committed/checkpointed partition."""
    partitions: list[dict] = []
    for title in titles:
        try:
            snapshot = latest_snapshot(
                title, request_json(client, VERSION_URL.format(title=title))
            )
            results = sync_title(
                snapshot,
                fetch_xml(client, snapshot.url),
                checkpoint_dir=checkpoint_dir,
                limit=limit,
                dry_run=dry_run,
                db_url=db_url,
                refresh_status=False,
            )
        except Exception as exc:
            partitions.append(
                {"title": title, "status": "failed", "error": str(exc)[-2000:]}
            )
            continue
        partitions.append(
            {
                "title": title,
                "status": "succeeded",
                "document_count": len(results),
                "documents": results,
            }
        )
    failures = [partition for partition in partitions if partition["status"] == "failed"]
    return {
        "status": "partial_failure" if failures else "succeeded",
        "partition_count": len(partitions),
        "failed_count": len(failures),
        "partitions": partitions,
    }


def _record_run_status(db_url: str | None, report: dict) -> None:
    failures = [
        f"title {partition['title']}: {partition['error']}"
        for partition in report["partitions"]
        if partition["status"] == "failed"
    ]
    with connect(db_url) as conn:
        if not failures:
            refresh_source_status(conn, {"govinfo:ecfr"})
        else:
            with conn.cursor() as cursor:
                cursor.execute(
                    """UPDATE legal_sources
                       SET last_attempted_at=now(), current_error=%s,
                           item_count=(SELECT COUNT(*) FROM legal_documents
                                       WHERE source_key='govinfo:ecfr'),
                           chunk_count=(SELECT COUNT(*) FROM legal_document_chunks c
                                        JOIN legal_documents d ON d.id=c.document_id
                                        WHERE d.source_key='govinfo:ecfr'),
                           embedded_chunk_count=(SELECT COUNT(*) FROM legal_document_chunks c
                                                 JOIN legal_documents d ON d.id=c.document_id
                                                 WHERE d.source_key='govinfo:ecfr'
                                                   AND c.embedding IS NOT NULL),
                           updated_at=now()
                       WHERE source_key='govinfo:ecfr'""",
                    ["; ".join(failures)[-2000:]],
                )
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or sync official eCFR section records")
    parser.add_argument("--title", type=int, action="append", dest="titles", help="repeatable; defaults to 26 and 42")
    parser.add_argument("--limit", type=int, help="maximum sections per title")
    parser.add_argument("--checkpoint-dir", default=".legalapp-checkpoints")
    parser.add_argument("--db-url")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="download and parse without database writes")
    mode.add_argument("--sync", action="store_true", help="upsert legal_documents and legal_document_chunks")
    args = parser.parse_args()
    if args.sync:
        init_schema(args.db_url)
        with connect(args.db_url) as conn:
            seed_catalog(conn, load_catalog())
            conn.commit()
    with httpx.Client(timeout=90, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml"}) as client:
        report = run_partitions(
            client,
            args.titles or DEFAULT_TITLES,
            checkpoint_dir=Path(args.checkpoint_dir),
            limit=args.limit,
            dry_run=args.preview,
            db_url=args.db_url,
        )
    if args.sync:
        _record_run_status(args.db_url, report)
    print(json.dumps(report, indent=2))
    if report["failed_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
