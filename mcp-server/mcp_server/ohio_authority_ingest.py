"""Respectful, authorization-scoped ingestion for official Ohio court materials.

This module intentionally discovers only direct documents linked by a small,
reviewed set of Supreme Court of Ohio index pages.  It never follows links from
commercial publishers, county sites, docket portals, or arbitrary pages.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from .authority_ingest import DEFAULT_USER_AGENT, FetchedDocument, html_main_text, ingest_document
from .database import connect
from .loader import init_schema
from .source_catalog import load_catalog, seed_catalog

DEFAULT_AUTHORIZATION_BASIS = "written authorization from Ohio Courts (user-confirmed 2026-07-31)"
DEFAULT_CONTACT = "legal-data-admin@example.invalid"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class OhioCourtsTarget:
    name: str
    index_url: str
    source_key: str
    document_type: str
    authority_tier: str
    practice_areas: tuple[str, ...]
    allowed_path_prefixes: tuple[str, ...]


DEFAULT_TARGETS = (
    OhioCourtsTarget("statewide-rules", "https://www.supremecourt.ohio.gov/laws-rules/ohio-rules-of-court/", "ohio:supreme-court-rules", "court_rule", "binding_primary", ("mediation", "probate", "litigation", "ethics"), ("/laws-rules/", "/docs/LegalResources/Rules/")),
    OhioCourtsTarget("probate-forms", "https://www.supremecourt.ohio.gov/forms/all-forms/probate", "ohio:probate-forms", "probate_form", "official_form", ("probate", "estate_planning", "medicaid"), ("/forms/", "/docs/LegalResources/Rules/", "/docs/JCS/")),
    OhioCourtsTarget("mediation", "https://www.supremecourt.ohio.gov/courts/services-to-courts/dispute-resolution/rules-legislation", "ohio:mediation-rules-forms", "mediation_material", "agency_guidance", ("mediation",), ("/courts/services-to-courts/dispute-resolution/", "/forms/", "/docs/JCS/")),
    OhioCourtsTarget("mediation-forms", "https://www.supremecourt.ohio.gov/forms/all-forms/dispute-resolution-and-mediation/61", "ohio:mediation-rules-forms", "mediation_form", "agency_guidance", ("mediation",), ("/forms/", "/docs/JCS/")),
    OhioCourtsTarget("opinions", "https://www.supremecourt.ohio.gov/opinions/", "ohio:supreme-court-opinions", "court_opinion", "binding_primary", ("all",), ("/opinions/", "/docs/ROD/", "/docs/Clerk/")),
)


@dataclass
class OhioCourtsConfig:
    contact: str
    authorization_basis: str = DEFAULT_AUTHORIZATION_BASIS
    targets: tuple[OhioCourtsTarget, ...] = DEFAULT_TARGETS
    allowed_host: str = "www.supremecourt.ohio.gov"
    min_interval_seconds: float = 1.0
    max_retries: int = 3
    max_download_bytes: int = DEFAULT_MAX_BYTES
    timeout_seconds: float = 45.0

    @property
    def user_agent(self) -> str:
        return f"LegalApp-OhioCourtsAuthoritySync/1.0 (+mailto:{self.contact}; {self.authorization_basis})"


@dataclass(frozen=True)
class DiscoveredDocument:
    target: OhioCourtsTarget
    canonical_url: str
    title: str

    @property
    def external_id(self) -> str:
        return "ohio-courts-" + hashlib.sha256(self.canonical_url.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class FetchResponse:
    status_code: int
    content: bytes
    media_type: str
    etag: str | None
    last_modified: datetime | None


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())))
        if tag.lower() == "a":
            self._href, self._text = None, []


def _normalized_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _is_allowed(url: str, target: OhioCourtsTarget, config: OhioCourtsConfig) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != config.allowed_host:
        return False
    path = parsed.path
    if not path.startswith(target.allowed_path_prefixes):
        return False
    return path.lower().endswith((".pdf", ".html", ".htm")) or "/opinions/" in path


def discover_documents(index_html: str, target: OhioCourtsTarget, config: OhioCourtsConfig) -> list[DiscoveredDocument]:
    parser = _LinkParser()
    parser.feed(index_html)
    parser.close()
    found: dict[str, DiscoveredDocument] = {}
    for href, text in parser.links:
        url = _normalized_url(urljoin(target.index_url, href))
        if _is_allowed(url, target, config):
            title = text or Path(urlparse(url).path).stem.replace("-", " ")
            found[url] = DiscoveredDocument(target, url, title)
    return [found[key] for key in sorted(found)]


def _parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_with_retries(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    config: OhioCourtsConfig,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchResponse:
    for attempt in range(config.max_retries + 1):
        response = client.get(url, headers=headers)
        if response.status_code == 304:
            return FetchResponse(304, b"", "", response.headers.get("etag"), _parse_http_date(response.headers.get("last-modified")))
        if response.status_code not in {429, 500, 502, 503, 504}:
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and int(length) > config.max_download_bytes:
                raise RuntimeError(f"refusing {url}: declared download exceeds {config.max_download_bytes} bytes")
            body = response.content
            if len(body) > config.max_download_bytes:
                raise RuntimeError(f"refusing {url}: download exceeds {config.max_download_bytes} bytes")
            return FetchResponse(response.status_code, body, response.headers.get("content-type", "").split(";", 1)[0].lower(), response.headers.get("etag"), _parse_http_date(response.headers.get("last-modified")))
        if attempt == config.max_retries:
            response.raise_for_status()
        retry_after = response.headers.get("retry-after")
        delay = float(retry_after) if retry_after and retry_after.isdigit() else min(60.0, 2.0 ** attempt)
        sleep(delay)
    raise AssertionError("unreachable")


def pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is pinned in requirements
        raise RuntimeError("PDF extraction requires pypdf; install mcp-server requirements") from exc
    return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(content)).pages).strip()


def to_fetched_document(result: FetchResponse, *, parser: str, now: datetime | None = None) -> FetchedDocument:
    if parser == "pdf":
        text = pdf_text(result.content)
    else:
        text = html_main_text(result.content.decode("utf-8", errors="replace"))
    if len(text) < 40:
        raise RuntimeError(f"extracted text is too short ({len(text)} characters)")
    return FetchedDocument(text, hashlib.sha256(text.encode("utf-8")).hexdigest(), result.media_type, now or datetime.now(timezone.utc), result.last_modified, result.etag)


def _existing_validators(conn: Any, source_key: str, external_id: str) -> dict[str, str]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT metadata FROM legal_documents WHERE source_key = %s AND external_id = %s", [source_key, external_id])
        row = cursor.fetchone()
    metadata = row[0] if row and row[0] else {}
    headers: dict[str, str] = {}
    if metadata.get("etag"):
        headers["If-None-Match"] = metadata["etag"]
    if metadata.get("last_modified_header"):
        headers["If-Modified-Since"] = metadata["last_modified_header"]
    return headers


def _checkpoint(conn: Any, target: OhioCourtsTarget, url: str, status: str, metadata: dict[str, Any]) -> None:
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO source_sync_states (source_key, partition_key, cursor_url, status, last_attempted_at, last_successful_sync_at, metadata)
            VALUES (%s, %s, %s, %s, now(), CASE WHEN %s = 'complete' THEN now() ELSE NULL END, %s::jsonb)
            ON CONFLICT (source_key, partition_key) DO UPDATE SET cursor_url = EXCLUDED.cursor_url, status = EXCLUDED.status,
              last_attempted_at = EXCLUDED.last_attempted_at, last_successful_sync_at = COALESCE(EXCLUDED.last_successful_sync_at, source_sync_states.last_successful_sync_at),
              metadata = source_sync_states.metadata || EXCLUDED.metadata, updated_at = now()
        """, [target.source_key, f"ohio-courts:{target.name}", url, status, status, json.dumps(metadata)])
    conn.commit()


def as_ingest_document(discovered: DiscoveredDocument, fetched: FetchedDocument, config: OhioCourtsConfig) -> dict[str, Any]:
    is_pdf = fetched.media_type == "application/pdf" or discovered.canonical_url.lower().endswith(".pdf")
    return {
        "source_key": discovered.target.source_key,
        "external_id": discovered.external_id,
        "document_type": discovered.target.document_type,
        "title": discovered.title,
        "citation": None,
        "jurisdiction": "OH",
        "authority_tier": discovered.target.authority_tier,
        "canonical_url": discovered.canonical_url,
        "parser": "pdf_text" if is_pdf else "html_main_text",
        "practice_areas": list(discovered.target.practice_areas),
        "metadata": {
            "publisher": "Supreme Court of Ohio",
            "official_status": "official",
            "authorization_basis": config.authorization_basis,
            "retrieval_scope": "official Ohio court materials linked from approved Supreme Court index pages",
            "source_index_url": discovered.target.index_url,
            "last_modified_header": fetched.source_modified_at.strftime("%a, %d %b %Y %H:%M:%S GMT") if fetched.source_modified_at else None,
        },
    }


def _resume_documents(conn: Any, documents: list[DiscoveredDocument]) -> list[DiscoveredDocument]:
    """Skip through a saved per-index cursor after an interrupted ordered run.

    A normal run deliberately does *not* use this shortcut: it revisits every
    discovered URL with conditional GET so newly inserted links cannot be missed.
    """
    cursors: dict[str, str] = {}
    with conn.cursor() as cursor:
        for target in {item.target for item in documents}:
            cursor.execute("SELECT cursor_url FROM source_sync_states WHERE source_key = %s AND partition_key = %s", [target.source_key, f"ohio-courts:{target.name}"])
            row = cursor.fetchone()
            if row and row[0]:
                cursors[target.name] = row[0]
    resumed: list[DiscoveredDocument] = []
    passed: set[str] = set()
    for item in documents:
        cursor_url = cursors.get(item.target.name)
        if cursor_url and item.target.name not in passed:
            if item.canonical_url != cursor_url:
                continue
            passed.add(item.target.name)
            continue
        resumed.append(item)
    return resumed


def crawl(
    config: OhioCourtsConfig,
    *,
    db_url: str | None = None,
    dry_run: bool = False,
    resume: bool = False,
    limit: int | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    if not config.contact or "@" not in config.contact:
        raise ValueError("a real contact email is required")
    owns_client = client is None
    client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=True, headers={"User-Agent": config.user_agent, "Accept": "text/html,application/pdf;q=0.9"})
    conn = None
    conn_context = None
    try:
        if not dry_run:
            init_schema(db_url)
            conn_context = connect(db_url)
            conn = conn_context.__enter__()
            seed_catalog(conn, load_catalog())
        documents: list[DiscoveredDocument] = []
        for target in config.targets:
            index = fetch_with_retries(client, target.index_url, headers={}, config=config, sleep=sleep)
            documents.extend(discover_documents(index.content.decode("utf-8", errors="replace"), target, config))
            sleep(config.min_interval_seconds)
        if resume and conn:
            documents = _resume_documents(conn, documents)
        documents = documents[:limit] if limit is not None else documents
        results: list[dict[str, Any]] = []
        for number, discovered in enumerate(documents):
            headers = _existing_validators(conn, discovered.target.source_key, discovered.external_id) if conn else {}
            response = fetch_with_retries(client, discovered.canonical_url, headers=headers, config=config, sleep=sleep)
            if response.status_code == 304:
                result = {"external_id": discovered.external_id, "canonical_url": discovered.canonical_url, "unchanged": True}
            else:
                parser = "pdf" if response.media_type == "application/pdf" or discovered.canonical_url.lower().endswith(".pdf") else "html"
                fetched = to_fetched_document(response, parser=parser)
                document = as_ingest_document(discovered, fetched, config)
                result = ({"external_id": document["external_id"], "canonical_url": document["canonical_url"], "characters": len(fetched.text), "dry_run": True} if dry_run else ingest_document(conn, document, fetched))
            results.append(result)
            if conn:
                _checkpoint(conn, discovered.target, discovered.canonical_url, "complete", {"authorization_basis": config.authorization_basis, "processed": number + 1})
            if number + 1 < len(documents):
                sleep(config.min_interval_seconds)
        return results
    finally:
        if conn_context is not None:
            conn_context.__exit__(None, None, None)
        if owns_client:
            client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl authorized official Ohio court authority pages")
    parser.add_argument("--contact", default=os.getenv("OHIO_COURTS_CRAWLER_CONTACT", DEFAULT_CONTACT))
    parser.add_argument("--authorization-basis", default=os.getenv("OHIO_COURTS_AUTHORIZATION_BASIS", DEFAULT_AUTHORIZATION_BASIS))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="Discover/fetch/extract without database writes")
    mode.add_argument("--sync", action="store_true", help="Fetch and upsert documents/chunks")
    parser.add_argument("--limit", type=int, help="Maximum discovered documents to process")
    parser.add_argument("--resume", action="store_true", help="Resume after the saved per-index checkpoint; ordinary sync rechecks all URLs conditionally")
    parser.add_argument("--db-url")
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum seconds between requests")
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=int(os.getenv("OHIO_COURT_MAX_DOWNLOAD_BYTES", str(DEFAULT_MAX_BYTES))),
        help="Maximum accepted bytes for any one official artifact",
    )
    args = parser.parse_args()
    config = OhioCourtsConfig(
        contact=args.contact,
        authorization_basis=args.authorization_basis,
        min_interval_seconds=args.delay,
        max_download_bytes=args.max_download_bytes,
    )
    print(json.dumps(crawl(config, db_url=args.db_url, dry_run=args.preview, limit=args.limit, resume=args.resume), indent=2))


if __name__ == "__main__":
    main()
