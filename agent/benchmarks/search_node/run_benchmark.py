#!/usr/bin/env python3
"""Run the Search Node acceptance queries against a live OpenSearch node.

`docs/search-node-operations.md` tells the operator to "run every benchmark
query (especially ACL-deny)" after an upgrade, after a snapshot restore, and
during rebuild quarantine recovery. This is that tool.

The fixtures are synthetic, so the run proves the engine, mapping, analyzer, and
ACL filter behave — not that the customer corpus is intact. It therefore loads
into its own disposable index generation and deletes it again, and never reads
or writes the aliases the agent serves from.

    python run_benchmark.py --url https://127.0.0.1:9200 --username lawhand_agent \
        --ca-path /etc/lawhand-agent/opensearch-ca.pem

The password is read from LAWHAND_BENCHMARK_OPENSEARCH_PASSWORD so it never
reaches a command line or a shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from clarity_agent.opensearch_engine import OpenSearchEngine
from clarity_agent.search_engine import DocumentChunk, SearchFilters, SearchRequest

FIXTURES = Path(__file__).resolve().parent
PASSWORD_VARIABLE = "LAWHAND_BENCHMARK_OPENSEARCH_PASSWORD"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def load_documents(path: Path) -> list[DocumentChunk]:
    """Read the fixture corpus into engine chunks.

    Every chunk of a document must share its generation and metadata, so the
    fixture's per-chunk records are normalized here rather than in the file.
    """
    modified_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    chunks: list[DocumentChunk] = []
    for ordinal, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        record = json.loads(line)
        chunks.append(
            DocumentChunk(
                document_id=record["document_id"],
                chunk_id=record["chunk_id"],
                share_id=record["share_id"],
                relative_path=record["relative_path"],
                filename=record["filename"],
                extension=record["extension"],
                content=record["content"],
                content_hash=f"benchmark-{record['document_id']}",
                modified_at=modified_at,
                mutation_generation=1,
                page_number=record.get("page_number"),
                section_path=tuple(record.get("section_path") or ()),
                ordinal=ordinal,
                matter_ids=tuple(record.get("matter_ids") or ()),
                acl_tokens=tuple(record.get("acl_tokens") or ()),
            )
        )
    if not chunks:
        raise ValueError("benchmark corpus is empty")
    return chunks


def evaluate(expectation: dict, response) -> CheckResult:
    """Compare one query's expectations against the engine's answer."""
    name = str(expectation.get("name") or "unnamed")
    hits = response.hits
    if "expected_hits" in expectation:
        expected = int(expectation["expected_hits"])
        if response.total != expected:
            return CheckResult(name, False, f"expected {expected} hits, got {response.total}")
        return CheckResult(name, True, f"{response.total} hits")
    if not hits:
        return CheckResult(name, False, "expected a hit, got none")
    top = hits[0]
    expected_document = expectation.get("expected_document_id")
    if expected_document is not None and top.document_id != expected_document:
        return CheckResult(name, False, f"top hit was {top.document_id!r}, wanted {expected_document!r}")
    expected_page = expectation.get("expected_page_number")
    if expected_page is not None and top.page_number != expected_page:
        return CheckResult(name, False, f"top hit was page {top.page_number}, wanted {expected_page}")
    return CheckResult(name, True, f"{top.document_id} page {top.page_number}")


def _request(expectation: dict) -> SearchRequest:
    filters = expectation.get("filters") or {}
    return SearchRequest(
        query=str(expectation["query"]),
        acl_tokens=tuple(expectation.get("acl_tokens") or ()),
        filters=SearchFilters(
            share_ids=tuple(filters.get("share_ids") or ()),
            matter_ids=tuple(filters.get("matter_ids") or ()),
            extensions=tuple(filters.get("extensions") or ()),
            document_ids=tuple(filters.get("document_ids") or ()),
        ),
    )


async def run(
    engine: OpenSearchEngine,
    chunks: Sequence[DocumentChunk],
    expectations: Sequence[dict],
) -> list[CheckResult]:
    result = await engine.bulk_index(list(chunks))
    if result.failed_ids:
        raise RuntimeError(f"benchmark corpus did not index: {len(result.failed_ids)} chunks failed")
    # BM25 scoring reads from the searchable segment, not the translog.
    await engine._request("POST", f"/{engine.write_alias}/_refresh")
    return [evaluate(item, await engine.search(_request(item))) for item in expectations]


async def main_async(args: argparse.Namespace) -> int:
    password = os.environ.get(PASSWORD_VARIABLE)
    if args.username and not password:
        print(f"error: set {PASSWORD_VARIABLE} for user {args.username!r}", file=sys.stderr)
        return 2
    # A disposable generation: the agent's aliases are never touched, so this is
    # safe to run against the live node during an upgrade or recovery window.
    prefix = args.index_prefix or f"lawhand-benchmark-{uuid.uuid4().hex[:10]}"
    engine = OpenSearchEngine(
        args.url,
        index_prefix=prefix,
        username=args.username or None,
        password=password or None,
        ca_path=args.ca_path or None,
        allow_insecure=args.allow_insecure,
    )
    try:
        results = await run(
            engine,
            load_documents(FIXTURES / "documents.jsonl"),
            json.loads((FIXTURES / "queries.json").read_text(encoding="utf-8")),
        )
    finally:
        try:
            await engine._request("DELETE", f"/{prefix}-*")
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask a result
            print(f"warning: could not delete benchmark indexes {prefix}-*: {exc}", file=sys.stderr)
        await engine.close()

    width = max(len(item.name) for item in results)
    for item in results:
        print(f"{'PASS' if item.passed else 'FAIL'}  {item.name.ljust(width)}  {item.detail}")
    failed = [item for item in results if not item.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="https://127.0.0.1:9200")
    parser.add_argument("--username", default="lawhand_agent")
    parser.add_argument("--ca-path", default="")
    parser.add_argument(
        "--index-prefix",
        default="",
        help="defaults to a unique disposable prefix that is deleted afterwards",
    )
    parser.add_argument(
        "--allow-insecure",
        action="store_true",
        help="permit plain http; for a disposable local node only",
    )
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
