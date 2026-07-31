"""Scheduler-facing eCFR CLI.  See ``python -m mcp_server.ecfr_ingest --help``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from .ecfr_adapter import DEFAULT_TITLES, USER_AGENT, VERSION_URL, fetch_xml, latest_snapshot, request_json, sync_title
from .loader import init_schema
from .source_catalog import load_catalog, seed_catalog
from .database import connect


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
    output = []
    with httpx.Client(timeout=90, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml"}) as client:
        for title in args.titles or DEFAULT_TITLES:
            snapshot = latest_snapshot(title, request_json(client, VERSION_URL.format(title=title)))
            output.extend(sync_title(snapshot, fetch_xml(client, snapshot.url), checkpoint_dir=Path(args.checkpoint_dir), limit=args.limit, dry_run=args.preview, db_url=args.db_url))
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
