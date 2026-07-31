"""Bounded ingestion of official North Dakota code and agency-policy materials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from .authority_ingest import FetchedDocument, ingest_document
from .database import connect
from .loader import init_schema
from .ohio_authority_ingest import _LinkParser, fetch_with_retries, to_fetched_document
from .source_catalog import load_catalog, seed_catalog

DEFAULT_CONTACT = "legal-data-admin@example.invalid"


@dataclass(frozen=True)
class NDTarget:
    name: str
    index_url: str
    source_key: str
    document_type: str
    authority_tier: str
    practice_areas: tuple[str, ...]
    host: str
    prefixes: tuple[str, ...]
    max_depth: int = 1


CODE_TARGETS = (
    NDTarget("century-code", "https://ndlegis.gov/prod/general-information/north-dakota-century-code/", "nd:century-code", "statute_chapter", "binding_primary", ("mediation", "contracts", "corporate", "probate"), "ndlegis.gov", ("/cencode/", "/prod/general-information/north-dakota-century-code/"), 2),
    NDTarget("administrative-code", "https://ndlegis.gov/agency-rules/north-dakota-administrative-code/index.html", "nd:administrative-code", "administrative_code_chapter", "binding_primary", ("mediation", "probate", "medicaid", "contracts", "corporate"), "ndlegis.gov", ("/agency-rules/north-dakota-administrative-code/", "/information/acdata/pdf/"), 2),
)
HHS_TARGET = NDTarget("hhs-policy-manuals", "https://www.hhs.nd.gov/resources/policy-manuals", "nd:hhs-policy-manuals", "agency_policy_manual", "agency_guidance", ("medicaid", "elder_law", "estate_planning", "probate"), "www.hhs.nd.gov", ("/resources/policy-manuals", "/sites/www/files/documents/"), 1)


@dataclass
class NDConfig:
    contact: str
    targets: tuple[NDTarget, ...] = CODE_TARGETS
    min_interval_seconds: float = 1.0
    max_retries: int = 3
    max_download_bytes: int = 25 * 1024 * 1024
    timeout_seconds: float = 45.0

    @property
    def user_agent(self) -> str:
        return f"LegalApp-NDAuthoritySync/1.0 (+mailto:{self.contact}; official-code retrieval)"


@dataclass(frozen=True)
class NDDocument:
    target: NDTarget
    url: str
    title: str
    depth: int

    @property
    def external_id(self) -> str:
        path = Path(urlparse(self.url).path).stem.lower()
        # Chapter names such as t30c12 are stable, human-auditable identifiers.
        stable = path if path else hashlib.sha256(self.url.encode()).hexdigest()[:16]
        return f"{self.target.name}:{stable}"


def _normal(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _allowed(url: str, target: NDTarget) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == target.host and parsed.path.startswith(target.prefixes) and (parsed.path.endswith((".pdf", ".html", ".htm", "/")) or parsed.path == target.index_url.rstrip("/").replace(f"https://{target.host}", ""))


def links(html: str, base_url: str, target: NDTarget) -> list[tuple[str, str]]:
    parser = _LinkParser()
    parser.feed(html)
    return sorted({(_normal(urljoin(base_url, href)), text) for href, text in parser.links if _allowed(_normal(urljoin(base_url, href)), target)})


def discover(index_html: str, target: NDTarget, fetch_html: Callable[[str], str]) -> list[NDDocument]:
    """Breadth-first discovery limited to official allowlisted index paths."""
    queue = [(target.index_url, index_html, 0)]
    seen = {target.index_url}
    result: dict[str, NDDocument] = {}
    while queue:
        base, html, depth = queue.pop(0)
        for url, title in links(html, base, target):
            if url in seen:
                continue
            seen.add(url)
            document = NDDocument(target, url, title or Path(urlparse(url).path).stem, depth + 1)
            if url.lower().endswith(".pdf"):
                result[url] = document
            elif depth + 1 < target.max_depth:
                queue.append((url, fetch_html(url), depth + 1))
    return [result[url] for url in sorted(result)]


def _checkpoint(conn: Any, doc: NDDocument, status: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO source_sync_states(source_key, partition_key, cursor_url, status, last_attempted_at, last_successful_sync_at, metadata)
            VALUES (%s, %s, %s, %s, now(), CASE WHEN %s = 'complete' THEN now() ELSE NULL END, %s::jsonb)
            ON CONFLICT (source_key, partition_key) DO UPDATE SET cursor_url=EXCLUDED.cursor_url, status=EXCLUDED.status,
              last_attempted_at=EXCLUDED.last_attempted_at, last_successful_sync_at=COALESCE(EXCLUDED.last_successful_sync_at,source_sync_states.last_successful_sync_at), metadata=source_sync_states.metadata || EXCLUDED.metadata, updated_at=now()
        """, [doc.target.source_key, f"nd-authority:{doc.target.name}", doc.url, status, status, json.dumps({"official_host": doc.target.host, "depth": doc.depth})])
    conn.commit()


def _record(doc: NDDocument, fetched: FetchedDocument) -> dict[str, Any]:
    return {"source_key": doc.target.source_key, "external_id": doc.external_id, "document_type": doc.target.document_type, "title": doc.title, "jurisdiction": "ND", "authority_tier": doc.target.authority_tier, "canonical_url": doc.url, "parser": "pdf_text", "practice_areas": list(doc.target.practice_areas), "metadata": {"publisher": "North Dakota Legislative Branch" if doc.target.host == "ndlegis.gov" else "North Dakota Health and Human Services", "official_status": "official", "source_index_url": doc.target.index_url, "discovery_depth": doc.depth, "etag": fetched.etag, "content_hash": fetched.content_hash}}


def crawl(config: NDConfig, *, dry_run: bool, db_url: str | None = None, limit: int | None = None, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    if "@" not in config.contact:
        raise ValueError("a real contact email is required")
    owns = client is None
    client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=True, headers={"User-Agent": config.user_agent, "Accept": "text/html,application/pdf;q=0.9"})
    conn = None
    conn_context = None
    try:
        if not dry_run:
            init_schema(db_url)
            conn_context = connect(db_url)
            conn = conn_context.__enter__()
            seed_catalog(conn, load_catalog())
        documents: list[NDDocument] = []
        for target in config.targets:
            first = fetch_with_retries(client, target.index_url, headers={}, config=config, sleep=sleep)
            def get_html(url: str) -> str:
                value = fetch_with_retries(client, url, headers={}, config=config, sleep=sleep)
                sleep(config.min_interval_seconds)
                return value.content.decode("utf-8", errors="replace")
            documents.extend(discover(first.content.decode("utf-8", errors="replace"), target, get_html))
            sleep(config.min_interval_seconds)
        documents = documents[:limit] if limit is not None else documents
        output = []
        for index, doc in enumerate(documents):
            response = fetch_with_retries(client, doc.url, headers={}, config=config, sleep=sleep)
            fetched = to_fetched_document(response, parser="pdf")
            item = {"external_id": doc.external_id, "canonical_url": doc.url, "characters": len(fetched.text), "dry_run": True} if dry_run else ingest_document(conn, _record(doc, fetched), fetched)
            output.append(item)
            if conn:
                _checkpoint(conn, doc, "complete")
            if index + 1 < len(documents):
                sleep(config.min_interval_seconds)
        return output
    finally:
        if conn_context is not None:
            conn_context.__exit__(None, None, None)
        if owns:
            client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest official North Dakota authority PDFs")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--sync", action="store_true")
    parser.add_argument("--include-hhs", action="store_true", help="Include official ND HHS policy-manual index")
    parser.add_argument("--contact", default=os.getenv("ND_AUTHORITY_CRAWLER_CONTACT", DEFAULT_CONTACT))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--db-url")
    args = parser.parse_args()
    targets = CODE_TARGETS + ((HHS_TARGET,) if args.include_hhs else ())
    print(json.dumps(crawl(NDConfig(args.contact, targets, args.delay), dry_run=args.preview, db_url=args.db_url, limit=args.limit), indent=2))


if __name__ == "__main__":
    main()
