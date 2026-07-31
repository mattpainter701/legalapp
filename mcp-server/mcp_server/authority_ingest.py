"""Reviewed-manifest ingestion for public statutes, rules, manuals, and guidance.

This is deliberately not a general web crawler.  Every fetched document must be
listed in ``authority_manifest.json`` and its parent source must pass the policy
checks in ``legal_sources.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .database import connect
from .loader import chunk_text, init_schema
from .source_catalog import load_catalog, seed_catalog

MANIFEST_PATH = Path(__file__).with_name("authority_manifest.json")
DEFAULT_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
DEFAULT_USER_AGENT = (
    "LegalApp-AuthoritySync/0.1 "
    "(+https://github.com/mattpainter701/legalapp; public legal-data research)"
)
SAFE_LOCAL_STORAGE_LICENSES = {
    "federal_public_domain",
    "public_domain_dedication",
    "permission_granted",
    "open_source_license",
    "api_terms",
    "membership_terms",
}
BLOCK_TAGS = {
    "address",
    "article",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
}
SKIP_TAGS = {"script", "style", "svg", "noscript", "nav", "footer", "header"}


class ManifestValidationError(ValueError):
    pass


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.in_main = False
        self.skip_depth = 0
        self.body_parts: list[str] = []
        self.main_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "body":
            self.in_body = True
        elif tag == "main":
            self.in_main = True
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        if tag in BLOCK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in BLOCK_TAGS:
            self._append("\n")
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        if tag == "main":
            self.in_main = False
        elif tag == "body":
            self.in_body = False

    def handle_data(self, data: str) -> None:
        self._append(data)

    def _append(self, value: str) -> None:
        if self.skip_depth or not self.in_body:
            return
        self.body_parts.append(value)
        if self.in_main:
            self.main_parts.append(value)

    def text(self) -> str:
        selected = self.main_parts or self.body_parts
        lines = []
        for raw_line in unescape("".join(selected)).splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line and (not lines or lines[-1] != line):
                lines.append(line)
        return "\n".join(lines)


@dataclass(frozen=True)
class FetchedDocument:
    text: str
    content_hash: str
    media_type: str
    retrieved_at: datetime
    source_modified_at: datetime | None
    etag: str | None


def html_main_text(value: str) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(value)
    parser.close()
    return parser.text()


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path else MANIFEST_PATH
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("schema_version") != 1:
        raise ManifestValidationError("manifest schema_version must be 1")
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ManifestValidationError("manifest documents must be a non-empty list")
    return manifest


def validate_manifest(manifest: dict[str, Any], catalog: dict[str, Any]) -> None:
    sources = {source["source_key"]: source for source in catalog["sources"]}
    seen: set[tuple[str, str]] = set()
    for document in manifest["documents"]:
        missing = {
            "source_key",
            "external_id",
            "document_type",
            "title",
            "canonical_url",
            "jurisdiction",
            "authority_tier",
            "parser",
            "practice_areas",
        } - document.keys()
        if missing:
            raise ManifestValidationError(
                f"manifest document is missing {', '.join(sorted(missing))}"
            )
        identity = (document["source_key"], document["external_id"])
        if identity in seen:
            raise ManifestValidationError(f"duplicate manifest document {identity}")
        seen.add(identity)

        source = sources.get(document["source_key"])
        if source is None:
            raise ManifestValidationError(f"unknown source {document['source_key']}")
        if source["access_type"] == "blocked_robots":
            raise ManifestValidationError(
                f"{document['source_key']} is blocked by the source access policy"
            )
        if source["license_status"] not in SAFE_LOCAL_STORAGE_LICENSES:
            raise ManifestValidationError(
                f"{document['source_key']} is not approved for local text storage"
            )
        if source["storage_policy"] not in {"mirror", "normalized_text"}:
            raise ManifestValidationError(
                f"{document['source_key']} storage policy does not permit normalized text"
            )
        if document["authority_tier"] != source["authority_tier"]:
            raise ManifestValidationError(
                f"{identity}: document authority_tier must match its source"
            )
        parsed = urlparse(document["canonical_url"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ManifestValidationError(f"{identity}: canonical_url must be https")
        if document["parser"] not in {"html_main_text", "pdf_text"}:
            raise ManifestValidationError(f"{identity}: unsupported parser")


def fetch_document(
    document: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    user_agent: str | None = None,
    max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
) -> FetchedDocument:
    owns_client = client is None
    client = client or httpx.Client(
        timeout=45.0,
        follow_redirects=True,
        headers={
            "User-Agent": user_agent or DEFAULT_USER_AGENT,
            "Accept": "text/html, application/xhtml+xml, application/pdf",
        },
    )
    try:
        response = client.get(document["canonical_url"])
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > max_document_bytes:
            raise RuntimeError(
                f"{document['canonical_url']} exceeds {max_document_bytes} bytes"
            )
        if len(response.content) > max_document_bytes:
            raise RuntimeError(
                f"{document['canonical_url']} exceeded {max_document_bytes} bytes"
            )
        media_type = response.headers.get("content-type", "text/html").split(";", 1)[0]
        if document["parser"] == "pdf_text":
            if media_type != "application/pdf" and not document["canonical_url"].lower().endswith(
                ".pdf"
            ):
                raise RuntimeError(
                    f"{document['canonical_url']} returned unsupported media type {media_type}"
                )
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise RuntimeError("pypdf is required for reviewed PDF ingestion") from exc
            text = "\n".join(
                page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages
            )
            media_type = "application/pdf"
        else:
            if media_type not in {"text/html", "application/xhtml+xml"}:
                raise RuntimeError(
                    f"{document['canonical_url']} returned unsupported media type {media_type}"
                )
            text = html_main_text(response.text)
        text = text.strip()
        if len(text) < 200:
            raise RuntimeError(
                f"{document['canonical_url']} produced too little readable text ({len(text)} chars)"
            )
        modified = None
        if response.headers.get("last-modified"):
            try:
                modified = parsedate_to_datetime(response.headers["last-modified"])
            except (TypeError, ValueError, OverflowError):
                modified = None
        return FetchedDocument(
            text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            media_type=media_type,
            retrieved_at=datetime.now(timezone.utc),
            source_modified_at=modified,
            etag=response.headers.get("etag"),
        )
    finally:
        if owns_client:
            client.close()


def ingest_document(conn: Any, document: dict[str, Any], fetched: FetchedDocument) -> dict[str, Any]:
    metadata = {
        "practice_areas": document["practice_areas"],
        "etag": fetched.etag,
        "manifest_parser": document["parser"],
    }
    metadata.update(document.get("metadata", {}))
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, content_hash
            FROM legal_documents
            WHERE source_key = %s AND external_id = %s
            """,
            [document["source_key"], document["external_id"]],
        )
        existing = cursor.fetchone()
        changed = existing is None or existing[1] != fetched.content_hash
        cursor.execute(
            """
            INSERT INTO legal_documents (
                source_key, external_id, document_type, title, citation,
                jurisdiction, authority_tier, document_status, publication_date,
                effective_date, canonical_url, source_modified_at, retrieved_at,
                content_hash, raw_media_type, parser_version, text_content, metadata
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s::jsonb
            )
            ON CONFLICT (source_key, external_id) DO UPDATE
            SET document_type = EXCLUDED.document_type,
                title = EXCLUDED.title,
                citation = EXCLUDED.citation,
                jurisdiction = EXCLUDED.jurisdiction,
                authority_tier = EXCLUDED.authority_tier,
                document_status = EXCLUDED.document_status,
                publication_date = EXCLUDED.publication_date,
                effective_date = EXCLUDED.effective_date,
                canonical_url = EXCLUDED.canonical_url,
                source_modified_at = EXCLUDED.source_modified_at,
                retrieved_at = EXCLUDED.retrieved_at,
                content_hash = EXCLUDED.content_hash,
                raw_media_type = EXCLUDED.raw_media_type,
                parser_version = EXCLUDED.parser_version,
                text_content = EXCLUDED.text_content,
                metadata = legal_documents.metadata || EXCLUDED.metadata,
                updated_at = now()
            RETURNING id
            """,
            [
                document["source_key"],
                document["external_id"],
                document["document_type"],
                document["title"],
                document.get("citation"),
                document["jurisdiction"],
                document["authority_tier"],
                document.get("document_status", "current"),
                document.get("publication_date"),
                document.get("effective_date"),
                document["canonical_url"],
                fetched.source_modified_at,
                fetched.retrieved_at,
                fetched.content_hash,
                fetched.media_type,
                "pdf-text-v1" if document["parser"] == "pdf_text" else "html-main-text-v1",
                fetched.text,
                json.dumps(metadata),
            ],
        )
        document_id = cursor.fetchone()[0]
        chunks_created = 0
        if changed:
            cursor.execute("DELETE FROM legal_document_chunks WHERE document_id = %s", [document_id])
            for index, content in enumerate(chunk_text(fetched.text)):
                cursor.execute(
                    """
                    INSERT INTO legal_document_chunks (
                        document_id, chunk_index, content, content_hash,
                        embedding, embedding_version, metadata
                    )
                    VALUES (%s, %s, %s, %s, NULL, 0, %s::jsonb)
                    """,
                    [
                        document_id,
                        index,
                        content,
                        hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        json.dumps({"practice_areas": document["practice_areas"]}),
                    ],
                )
                chunks_created += 1
        cursor.execute(
            """
            UPDATE legal_sources
            SET last_attempted_at = now(), last_successful_sync_at = now(),
                current_error = NULL,
                item_count = (
                    SELECT COUNT(*) FROM legal_documents WHERE source_key = %s
                ),
                chunk_count = (
                    SELECT COUNT(*) FROM legal_document_chunks c
                    JOIN legal_documents d ON d.id = c.document_id
                    WHERE d.source_key = %s
                ),
                embedded_chunk_count = (
                    SELECT COUNT(*) FROM legal_document_chunks c
                    JOIN legal_documents d ON d.id = c.document_id
                    WHERE d.source_key = %s AND c.embedding IS NOT NULL
                ),
                updated_at = now()
            WHERE source_key = %s
            """,
            [document["source_key"]] * 4,
        )
    conn.commit()
    return {
        "source_key": document["source_key"],
        "external_id": document["external_id"],
        "changed": changed,
        "chunks_created": chunks_created,
        "characters": len(fetched.text),
        "content_hash": fetched.content_hash,
    }


def selected_documents(
    manifest: dict[str, Any], source_keys: list[str] | None, limit: int | None
) -> list[dict[str, Any]]:
    documents = manifest["documents"]
    if source_keys:
        wanted = set(source_keys)
        documents = [document for document in documents if document["source_key"] in wanted]
        missing = wanted - {document["source_key"] for document in documents}
        if missing:
            raise ManifestValidationError(
                f"no manifest documents for source(s): {', '.join(sorted(missing))}"
            )
    return documents[:limit] if limit is not None else documents


def preview(documents: list[dict[str, Any]], delay_seconds: float = 1.0) -> list[dict[str, Any]]:
    results = []
    user_agent = os.getenv("LEGAL_SOURCE_USER_AGENT", DEFAULT_USER_AGENT)
    with httpx.Client(
        timeout=45.0,
        follow_redirects=True,
        headers={"User-Agent": user_agent, "Accept": "text/html"},
    ) as client:
        for index, document in enumerate(documents):
            fetched = fetch_document(document, client=client)
            results.append(
                {
                    "source_key": document["source_key"],
                    "external_id": document["external_id"],
                    "characters": len(fetched.text),
                    "chunks": len(chunk_text(fetched.text)),
                    "content_hash": fetched.content_hash,
                    "media_type": fetched.media_type,
                }
            )
            if delay_seconds > 0 and index + 1 < len(documents):
                time.sleep(delay_seconds)
    return results


def sync_documents(
    documents: list[dict[str, Any]], catalog: dict[str, Any], db_url: str | None
) -> list[dict[str, Any]]:
    init_schema(db_url)
    user_agent = os.getenv("LEGAL_SOURCE_USER_AGENT", DEFAULT_USER_AGENT)
    results = []
    with connect(db_url) as conn:
        seed_catalog(conn, catalog)
        with httpx.Client(
            timeout=45.0,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "text/html"},
        ) as client:
            for index, document in enumerate(documents):
                try:
                    fetched = fetch_document(document, client=client)
                    results.append(ingest_document(conn, document, fetched))
                except Exception as exc:
                    conn.rollback()
                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE legal_sources
                            SET last_attempted_at = now(), current_error = %s, updated_at = now()
                            WHERE source_key = %s
                            """,
                            [str(exc)[:2000], document["source_key"]],
                        )
                    conn.commit()
                    raise
                if index + 1 < len(documents):
                    time.sleep(float(os.getenv("LEGAL_SOURCE_REQUEST_DELAY_SECONDS", "1")))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch reviewed public-authority manifests")
    parser.add_argument("--manifest")
    parser.add_argument("--catalog")
    parser.add_argument("--source-key", action="append", dest="source_keys")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--preview", action="store_true", help="Fetch and parse without database writes")
    parser.add_argument("--sync", action="store_true", help="Fetch and upsert documents/chunks")
    parser.add_argument("--db-url")
    args = parser.parse_args()
    if args.preview and args.sync:
        parser.error("choose either --preview or --sync")

    catalog = load_catalog(args.catalog)
    manifest = load_manifest(args.manifest)
    validate_manifest(manifest, catalog)
    documents = selected_documents(manifest, args.source_keys, args.limit)

    if args.preview:
        print(json.dumps(preview(documents), indent=2))
    elif args.sync:
        print(json.dumps(sync_documents(documents, catalog, args.db_url), indent=2))
    else:
        print(
            json.dumps(
                {
                    "status": "valid",
                    "document_count": len(documents),
                    "source_keys": sorted({doc["source_key"] for doc in documents}),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
