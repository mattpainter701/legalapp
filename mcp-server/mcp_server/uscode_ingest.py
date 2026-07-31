"""Production CLI for official U.S. Code USLM section ingestion.

The discovery/download primitives live in :mod:`mcp_server.uscode_adapter` so they
can be reused by a scheduler.  This module is the stable operational entry point:

    python -m mcp_server.uscode_ingest --preview --title 26 --limit 25
    python -m mcp_server.uscode_ingest --sync --title 26 --db-url postgresql://...
"""

from __future__ import annotations

import argparse
import json
from typing import Iterable

from .uscode_adapter import (
    DEFAULT_TITLES,
    discover_artifacts,
    download_artifact,
    fetch_download_page,
    iter_sections,
    sync_official_titles,
)


def preview_official_titles(
    *, titles: Iterable[int] = DEFAULT_TITLES, limit: int | None = None
) -> list[dict[str, object]]:
    """Download, validate, and parse official artifacts without database writes."""
    artifacts = discover_artifacts(fetch_download_page(), titles)
    results: list[dict[str, object]] = []
    for artifact in artifacts:
        downloaded = download_artifact(artifact)
        sections = list(iter_sections(downloaded, limit=limit))
        results.append(
            {
                "title": artifact.title,
                "release_point": artifact.release_point,
                "sections": len(sections),
                "artifact_sha256": downloaded.sha256,
                "first_external_id": sections[0].external_id if sections else None,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest official U.S. Code USLM title ZIPs")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="Download and parse without database writes")
    mode.add_argument("--sync", action="store_true", help="Download and resumably upsert sections")
    parser.add_argument("--title", type=int, action="append", dest="titles", help="Repeat for each U.S. Code title")
    parser.add_argument("--limit", type=int, help="Maximum sections per selected title")
    parser.add_argument("--db-url", help="PostgreSQL URL; used only with --sync")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    titles = args.titles or DEFAULT_TITLES
    if args.preview:
        print(json.dumps(preview_official_titles(titles=titles, limit=args.limit), indent=2))
    else:
        print(json.dumps(sync_official_titles(titles=titles, db_url=args.db_url, limit=args.limit), indent=2))


if __name__ == "__main__":
    main()
