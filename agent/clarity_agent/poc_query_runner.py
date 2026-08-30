"""Run privacy-preserving local search queries from JSONL input."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TextIO

from clarity_agent.local_index import LocalSearchIndex


MAX_QUERY_RECORDS = 10_000
MAX_INPUT_LINE_CHARS = 100_000
OPAQUE_QUERY_ID = re.compile(
    r"(?:q[0-9]{1,8}|[0-9a-f]{64}|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    flags=re.IGNORECASE,
)


def _identity_part(value: Any, *, path: bool = False) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if path:
        text = text.replace("/", "\\")
    return text.casefold()


def opaque_doc_id(share_id: str, relative_path: str) -> str:
    """Return a stable identifier that discloses no source naming data."""
    identity = (
        _identity_part(share_id) + "\0" + _identity_part(relative_path, path=True)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _records(stream: Iterable[str]) -> Iterable[dict[str, Any]]:
    records = 0
    for number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        if len(line) > MAX_INPUT_LINE_CHARS:
            raise ValueError(f"line {number} exceeds the input size limit")
        records += 1
        if records > MAX_QUERY_RECORDS:
            raise ValueError("query manifest exceeds the record limit")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"line {number} is not a JSON object")
        yield value


async def run_queries(
    db_path: str | Path,
    input_stream: Iterable[str],
    output_stream: TextIO,
) -> None:
    """Search each input record and write one evaluator-compatible JSONL row."""
    index = LocalSearchIndex(str(db_path))
    await index.init_readonly()
    try:
        for record in _records(input_stream):
            query_id = str(record.get("query_id") or "")
            query = record.get("query")
            share_id = str(record.get("share_id") or "")
            share_path = str(record.get("share_path") or "")
            if (
                not OPAQUE_QUERY_ID.fullmatch(query_id)
                or not isinstance(query, str)
                or not share_id
                or not share_path
            ):
                raise ValueError(
                    "each query requires an opaque query_id, query, share_id, "
                    "and share_path"
                )
            if len(share_id) > 256 or len(share_path) > 32_768:
                raise ValueError("query scope exceeds its size limit")
            folder = record.get("folder")
            if folder is not None and len(str(folder)) > 32_768:
                raise ValueError("query folder exceeds its size limit")
            extensions = record.get("extensions")
            if extensions is not None and (
                not isinstance(extensions, list)
                or len(extensions) > 50
                or any(len(str(value)) > 16 for value in extensions)
            ):
                raise ValueError("query extensions are invalid")
            started = time.perf_counter()
            result = await index.search(
                query,
                [{"share_id": share_id, "folder_path": folder}],
                [{"share_id": share_id, "share_path": share_path}],
                extensions,
                int(record.get("limit") or 20),
            )
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            results = [
                {
                    "doc_id": opaque_doc_id(hit["share_id"], hit["relative_path"]),
                    "page": hit.get("page_number"),
                }
                for hit in result["hits"]
            ]
            output_stream.write(
                json.dumps(
                    {
                        "query_id": query_id,
                        "latency_ms": latency_ms,
                        "results": results,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    finally:
        await index.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", help="existing local SQLite index")
    parser.add_argument("queries", help="query JSONL, or - for stdin")
    parser.add_argument("--output", help="result JSONL, default stdout")
    args = parser.parse_args(argv)
    input_handle = (
        sys.stdin
        if args.queries == "-"
        else Path(args.queries).open("r", encoding="utf-8")
    )
    output_handle = (
        sys.stdout if not args.output else Path(args.output).open("w", encoding="utf-8")
    )
    try:
        asyncio.run(run_queries(args.db, input_handle, output_handle))
    finally:
        if input_handle is not sys.stdin:
            input_handle.close()
        if output_handle is not sys.stdout:
            output_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
