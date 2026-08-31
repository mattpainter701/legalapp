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
from .public_lineage import public_authority_metadata, require_public_candidate_version
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
    raw_content: bytes = b""
    raw_content_hash: str = ""
    resolved_url: str = ""


def html_main_text(value: str) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(value)
    parser.close()
    return parser.text()


def extracted_text_status(value: str) -> str:
    """Reject a known class of PDF font-map output before it reaches embeddings."""

    sample = value[:100_000]
    glyph_references = len(re.findall(r"/(?:i255|\d+)", sample))
    readable_words = len(re.findall(r"[A-Za-z]{3,}", sample))
    if glyph_references > 100 and glyph_references > readable_words:
        return "blocked_font_map"
    return "passed_heuristic"


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
            "official_status",
            "parser",
            "parser_version",
            "acquisition_basis",
            "coverage_notes",
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
        if document["official_status"] != source["official_status"]:
            raise ManifestValidationError(
                f"{identity}: document official_status must match its source"
            )
        parsed = urlparse(document["canonical_url"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise ManifestValidationError(f"{identity}: canonical_url must be https")
        if document["parser"] not in {"html_main_text", "pdf_text"}:
            raise ManifestValidationError(f"{identity}: unsupported parser")
        if not isinstance(document.get("sync_enabled", True), bool):
            raise ManifestValidationError(f"{identity}: sync_enabled must be boolean")
        if document.get("sync_enabled") is False:
            reason = document.get("sync_disabled_reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ManifestValidationError(
                    f"{identity}: sync_disabled_reason must explain why sync is disabled"
                )
        for field in ("parser_version", "acquisition_basis", "coverage_notes"):
            if not isinstance(document[field], str) or not document[field].strip():
                raise ManifestValidationError(f"{identity}: {field} must be non-empty")
        for field in (
            "publication_date",
            "effective_date",
            "source_version_date",
            "coverage_start_date",
            "coverage_end_date",
        ):
            value = document.get(field)
            if value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except (TypeError, ValueError) as exc:
                    raise ManifestValidationError(
                        f"{identity}: {field} must be YYYY-MM-DD"
                    ) from exc
        if document.get("source_index_url"):
            index_url = urlparse(document["source_index_url"])
            if index_url.scheme != "https" or not index_url.netloc:
                raise ManifestValidationError(
                    f"{identity}: source_index_url must be https"
                )


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
            if media_type != "application/pdf" and not document[
                "canonical_url"
            ].lower().endswith(".pdf"):
                raise RuntimeError(
                    f"{document['canonical_url']} returned unsupported media type {media_type}"
                )
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise RuntimeError(
                    "pypdf is required for reviewed PDF ingestion"
                ) from exc
            text = "\n".join(
                page.extract_text() or ""
                for page in PdfReader(BytesIO(response.content)).pages
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
            raw_content=response.content,
            raw_content_hash=hashlib.sha256(response.content).hexdigest(),
            resolved_url=str(response.url),
        )
    finally:
        if owns_client:
            client.close()


def ingest_document(
    conn: Any,
    document: dict[str, Any],
    fetched: FetchedDocument,
    *,
    corpus_version: str | None = None,
) -> dict[str, Any]:
    extraction_status = extracted_text_status(fetched.text)
    if extraction_status != "passed_heuristic":
        raise RuntimeError(
            f"{document['canonical_url']} text extraction is {extraction_status}; "
            "raw artifact requires a reviewed parser before ingestion"
        )
    metadata = {
        "practice_areas": document["practice_areas"],
        "etag": fetched.etag,
        "manifest_parser": document["parser"],
        "official_status": document["official_status"],
        "acquisition_basis": document["acquisition_basis"],
        "coverage_notes": document["coverage_notes"],
        "source_index_url": document.get("source_index_url"),
        "raw_content_hash": fetched.raw_content_hash,
        "resolved_url": fetched.resolved_url,
        "text_extraction_status": extraction_status,
    }
    for field in (
        "publication_year",
        "source_version_date",
        "coverage_start_date",
        "coverage_end_date",
    ):
        if field in document:
            metadata[field] = document[field]
    metadata = public_authority_metadata(document.get("metadata"), trusted=metadata)
    with conn.cursor() as cursor:
        requested_version = (
            str(corpus_version).strip()
            if corpus_version is not None
            else os.environ.get("AUTHORITY_INGEST_CORPUS_VERSION", "").strip()
        )
        corpus_version = require_public_candidate_version(
            conn,
            source_key=document["source_key"],
            requested_version=requested_version,
            error_message=(
                "source requires a reviewed public-authority admission before ingestion"
            ),
        )
        cursor.execute(
            """
            SELECT id, content_hash
            FROM legal_documents
            WHERE source_key = %s AND external_id = %s AND corpus_version = %s
            """,
            [document["source_key"], document["external_id"], corpus_version],
        )
        existing = cursor.fetchone()
        changed = existing is None or existing[1] != fetched.content_hash
        cursor.execute(
            """
            INSERT INTO legal_documents (
                source_key, external_id, document_type, title, citation,
                jurisdiction, authority_tier, document_status, publication_date,
                effective_date, canonical_url, source_modified_at, retrieved_at,
                content_hash, raw_media_type, parser_version, text_content, metadata,
                corpus_version
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s::jsonb, %s
            )
            ON CONFLICT (source_key, external_id, corpus_version) DO UPDATE
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
                corpus_version = EXCLUDED.corpus_version,
                metadata = EXCLUDED.metadata,
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
                document["parser_version"],
                fetched.text,
                json.dumps(metadata),
                corpus_version,
            ],
        )
        document_id = cursor.fetchone()[0]
        chunks_created = 0
        if changed:
            cursor.execute(
                "DELETE FROM legal_document_chunks WHERE document_id = %s",
                [document_id],
            )
            for index, content in enumerate(chunk_text(fetched.text)):
                cursor.execute(
                    """
                    INSERT INTO legal_document_chunks (
                        document_id, chunk_index, content, content_hash,
                        embedding, embedding_version, metadata, corpus_version
                    )
                    VALUES (%s, %s, %s, %s, NULL, 0, %s::jsonb, %s)
                    """,
                    [
                        document_id,
                        index,
                        content,
                        hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        json.dumps({"practice_areas": document["practice_areas"]}),
                        corpus_version,
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
        cursor.execute(
            """
            INSERT INTO authority_harvest_events
                (source_key, partition_key, corpus_version, external_id, content_hash,
                 cursor_before, cursor_after, event_status, citation, court,
                 effective_date, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'accepted', %s, %s, %s, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            [
                document["source_key"],
                f"manifest:{document['source_key']}",
                corpus_version,
                document["external_id"],
                fetched.content_hash,
                document.get("canonical_url"),
                document.get("canonical_url"),
                document.get("citation"),
                document.get("court_id"),
                document.get("effective_date"),
                json.dumps(
                    {
                        "namespace": "public-authority",
                        "parser_version": document["parser_version"],
                    }
                ),
            ],
        )
        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO authority_harvest_events
                  (source_key, partition_key, corpus_version, external_id,
                   content_hash, event_status, metadata)
                VALUES (%s, %s, %s, %s, %s, 'duplicate', %s::jsonb)
                ON CONFLICT DO NOTHING
            """,
                [
                    document["source_key"],
                    f"manifest:{document['source_key']}",
                    corpus_version,
                    document["external_id"],
                    fetched.content_hash,
                    json.dumps({"namespace": "public-authority", "replay": True}),
                ],
            )
        cursor.execute(
            """INSERT INTO source_sync_states
                 (source_key, partition_key, checkpoint_at, cursor_url, status,
                  last_attempted_at, last_successful_sync_at, rows_processed,
                  last_cursor_hash, next_retry_at)
               VALUES (%s, %s, now(), %s, 'complete', now(), now(), 1, %s, NULL)
               ON CONFLICT (source_key, partition_key) DO UPDATE SET
                 checkpoint_at=EXCLUDED.checkpoint_at, cursor_url=EXCLUDED.cursor_url,
                 status='complete', last_attempted_at=EXCLUDED.last_attempted_at,
                 last_successful_sync_at=EXCLUDED.last_successful_sync_at,
                 rows_processed=source_sync_states.rows_processed + 1,
                 last_cursor_hash=EXCLUDED.last_cursor_hash, next_retry_at=NULL,
                 retry_count=0, updated_at=now()""",
            [
                document["source_key"],
                f"manifest:{document['source_key']}",
                document["canonical_url"],
                fetched.content_hash,
            ],
        )
        cursor.execute(
            """INSERT INTO authority_harvest_checkpoints
                 (source_key, partition_key, corpus_version, cursor_url, cursor_hash,
                  status, retry_count, last_successful_harvest_at)
               VALUES (%s, %s, %s, %s, %s, 'complete', 0, now())
               ON CONFLICT (source_key, partition_key, corpus_version) DO UPDATE SET
                 cursor_url=EXCLUDED.cursor_url, cursor_hash=EXCLUDED.cursor_hash,
                 status='complete', retry_count=0, next_retry_at=NULL,
                 dead_letter_at=NULL, last_successful_harvest_at=now(), updated_at=now()""",
            [
                document["source_key"],
                f"manifest:{document['source_key']}",
                corpus_version,
                document["canonical_url"],
                fetched.content_hash,
            ],
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
    manifest: dict[str, Any],
    source_keys: list[str] | None,
    limit: int | None,
    *,
    for_sync: bool = False,
) -> list[dict[str, Any]]:
    documents = manifest["documents"]
    if source_keys:
        wanted = set(source_keys)
        documents = [
            document for document in documents if document["source_key"] in wanted
        ]
        missing = wanted - {document["source_key"] for document in documents}
        if missing:
            raise ManifestValidationError(
                f"no manifest documents for source(s): {', '.join(sorted(missing))}"
            )
    if for_sync:
        documents = [
            document for document in documents if document.get("sync_enabled", True)
        ]
    return documents[:limit] if limit is not None else documents


def _artifact_basename(document: dict[str, Any]) -> str:
    value = f"{document['source_key']}--{document['external_id']}"
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-_").lower()


def _raw_extension(fetched: FetchedDocument) -> str:
    if fetched.media_type == "application/pdf":
        return ".pdf"
    if fetched.media_type in {"text/html", "application/xhtml+xml"}:
        return ".html"
    return ".bin"


def _write_preview_artifact(
    output_dir: Path,
    document: dict[str, Any],
    fetched: FetchedDocument,
) -> tuple[str, str]:
    raw_dir = output_dir / "raw"
    text_dir = output_dir / "normalized"
    raw_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    basename = _artifact_basename(document)
    raw_path = raw_dir / f"{basename}{_raw_extension(fetched)}"
    text_path = text_dir / f"{basename}.txt"
    raw_path.write_bytes(fetched.raw_content)
    # Keep the retained normalized artifact byte-for-byte consistent across
    # platforms so its SHA-256 is the same canonical content hash recorded in
    # the preview manifest and later stored in Postgres.
    text_path.write_text(fetched.text, encoding="utf-8", newline="\n")
    return raw_path.relative_to(output_dir).as_posix(), text_path.relative_to(
        output_dir
    ).as_posix()


def preview(
    documents: list[dict[str, Any]],
    delay_seconds: float = 1.0,
    output_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    results = []
    output_path = Path(output_dir).resolve() if output_dir else None
    if output_path:
        output_path.mkdir(parents=True, exist_ok=True)
    user_agent = os.getenv("LEGAL_SOURCE_USER_AGENT", DEFAULT_USER_AGENT)
    with httpx.Client(
        timeout=45.0,
        follow_redirects=True,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html, application/xhtml+xml, application/pdf",
        },
    ) as client:
        for index, document in enumerate(documents):
            fetched = fetch_document(document, client=client)
            raw_path = normalized_path = None
            if output_path:
                raw_path, normalized_path = _write_preview_artifact(
                    output_path, document, fetched
                )
            result = {
                "source_key": document["source_key"],
                "external_id": document["external_id"],
                "document_type": document["document_type"],
                "title": document["title"],
                "citation": document.get("citation"),
                "canonical_url": document["canonical_url"],
                "source_index_url": document.get("source_index_url"),
                "resolved_url": fetched.resolved_url,
                "jurisdiction": document["jurisdiction"],
                "authority_tier": document["authority_tier"],
                "official_status": document["official_status"],
                "document_status": document.get("document_status", "current"),
                "publication_year": document.get("publication_year"),
                "publication_date": document.get("publication_date"),
                "effective_date": document.get("effective_date"),
                "source_version_date": document.get("source_version_date"),
                "coverage_start_date": document.get("coverage_start_date"),
                "coverage_end_date": document.get("coverage_end_date"),
                "acquisition_basis": document["acquisition_basis"],
                "coverage_notes": document["coverage_notes"],
                "parser": document["parser"],
                "parser_version": document["parser_version"],
                "retrieved_at": fetched.retrieved_at.isoformat(),
                "source_modified_at": (
                    fetched.source_modified_at.isoformat()
                    if fetched.source_modified_at
                    else None
                ),
                "etag": fetched.etag,
                "media_type": fetched.media_type,
                "bytes": len(fetched.raw_content),
                "characters": len(fetched.text),
                "chunks": len(chunk_text(fetched.text)),
                "text_extraction_status": extracted_text_status(fetched.text),
                "embedding_readiness": (
                    "pending_chunk_review"
                    if extracted_text_status(fetched.text) == "passed_heuristic"
                    else "blocked_parser_quality"
                ),
                "raw_content_hash": fetched.raw_content_hash,
                "content_hash": fetched.content_hash,
                "raw_path": raw_path,
                "normalized_path": normalized_path,
            }
            results.append(result)
            if delay_seconds > 0 and index + 1 < len(documents):
                time.sleep(delay_seconds)
    if output_path:
        report = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "document_count": len(results),
            "documents": results,
        }
        (output_path / "preview-manifest.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    return results


def sync_documents(
    documents: list[dict[str, Any]], catalog: dict[str, Any], db_url: str | None
) -> list[dict[str, Any]]:
    init_schema(db_url)
    user_agent = os.getenv("LEGAL_SOURCE_USER_AGENT", DEFAULT_USER_AGENT)
    results = []
    with connect(db_url) as conn:
        seed_catalog(conn, catalog)
        candidate_versions = {
            source_key: require_public_candidate_version(
                conn,
                source_key=source_key,
                error_message=(
                    "source requires a reviewed public-authority admission before sync"
                ),
            )
            for source_key in {str(document["source_key"]) for document in documents}
        }
        with httpx.Client(
            timeout=45.0,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "text/html"},
        ) as client:
            for index, document in enumerate(documents):
                corpus_version = candidate_versions[str(document["source_key"])]
                partition_key = f"manifest:{document['source_key']}"
                with conn.cursor() as cursor:
                    cursor.execute(
                        """SELECT cursor_url, status FROM authority_harvest_checkpoints
                                       WHERE source_key=%s AND partition_key=%s
                                       AND corpus_version=%s""",
                        [document["source_key"], partition_key, corpus_version],
                    )
                    cursor.fetchone()
                # A successful URL is only a checkpoint hint.  Re-fetch it so
                # ETag/Last-Modified/hash semantics can detect upstream change
                # and cadence staleness; dedupe remains enforced at ingest.
                try:
                    fetched = fetch_document(document, client=client)
                    results.append(
                        ingest_document(
                            conn,
                            document,
                            fetched,
                            corpus_version=corpus_version,
                        )
                    )
                except Exception as exc:
                    conn.rollback()
                    with conn.cursor() as cursor:
                        failure_text = str(exc)[:2000]
                        cursor.execute(
                            "SELECT retry_count FROM source_sync_states WHERE source_key=%s AND partition_key=%s",
                            [
                                document["source_key"],
                                f"manifest:{document['source_key']}",
                            ],
                        )
                        retry_row = cursor.fetchone()
                        retry_count = int(retry_row[0] or 0) + 1 if retry_row else 1
                        event_status = (
                            "quarantined"
                            if any(
                                marker in failure_text.lower()
                                for marker in (
                                    "extraction",
                                    "unsupported media",
                                    "too little readable",
                                )
                            )
                            else (
                                "dead_letter"
                                if retry_count >= 3
                                else "retryable_failure"
                            )
                        )
                        cursor.execute(
                            """
                            INSERT INTO authority_harvest_events
                                (source_key, partition_key, corpus_version, external_id,
                                 cursor_before, cursor_after, event_status, retry_count,
                                 quarantine_reason, court, metadata)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                            ON CONFLICT DO NOTHING
                            """,
                            [
                                document["source_key"],
                                f"manifest:{document['source_key']}",
                                corpus_version,
                                document["external_id"],
                                document.get("canonical_url"),
                                document.get("canonical_url"),
                                event_status,
                                retry_count,
                                failure_text
                                if event_status in {"quarantined", "dead_letter"}
                                else None,
                                document.get("court_id"),
                                json.dumps({"namespace": "public-authority"}),
                            ],
                        )
                        cursor.execute(
                            """INSERT INTO source_sync_states
                                 (source_key, partition_key, status, last_attempted_at,
                                  last_error, retry_count, next_retry_at, dead_letter_count)
                                 VALUES (%s, %s, %s, now(), %s, %s,
                                       CASE WHEN %s IN ('quarantined', 'dead_letter')
                                            THEN NULL ELSE now() + interval '5 minutes' END,
                                       CASE WHEN %s IN ('quarantined', 'dead_letter') THEN 1 ELSE 0 END)
                               ON CONFLICT (source_key, partition_key) DO UPDATE SET
                                 status=EXCLUDED.status, last_attempted_at=now(),
                                 last_error=EXCLUDED.last_error,
                                 retry_count=EXCLUDED.retry_count,
                                 next_retry_at=CASE WHEN EXCLUDED.status IN ('quarantined', 'dead_letter')
                                                    THEN NULL ELSE now() + interval '5 minutes' END,
                                 dead_letter_count=source_sync_states.dead_letter_count
                                   + CASE WHEN EXCLUDED.status IN ('quarantined', 'dead_letter') THEN 1 ELSE 0 END,
                                 updated_at=now()""",
                            [
                                document["source_key"],
                                f"manifest:{document['source_key']}",
                                event_status,
                                failure_text,
                                retry_count,
                                event_status,
                                event_status,
                            ],
                        )
                        if corpus_version:
                            cursor.execute(
                                """INSERT INTO authority_harvest_checkpoints
                                 (source_key, partition_key, corpus_version, cursor_url,
                                 status, retry_count, next_retry_at, dead_letter_at)
                               VALUES (%s, %s, %s, %s, %s, %s,
                                       CASE WHEN %s IN ('quarantined', 'dead_letter')
                                            THEN NULL ELSE now() + interval '5 minutes' END,
                                       CASE WHEN %s='dead_letter' THEN now() ELSE NULL END)
                               ON CONFLICT (source_key, partition_key, corpus_version) DO UPDATE SET
                                 status=EXCLUDED.status, retry_count=EXCLUDED.retry_count,
                                 next_retry_at=EXCLUDED.next_retry_at,
                                 dead_letter_at=EXCLUDED.dead_letter_at, updated_at=now()""",
                                [
                                    document["source_key"],
                                    partition_key,
                                    corpus_version,
                                    document.get("canonical_url"),
                                    event_status,
                                    retry_count,
                                    event_status,
                                    event_status,
                                ],
                            )
                        cursor.execute(
                            """
                            UPDATE legal_sources
                            SET last_attempted_at = now(), current_error = %s, updated_at = now()
                            WHERE source_key = %s
                            """,
                            [failure_text, document["source_key"]],
                        )
                    conn.commit()
                    results.append(
                        {
                            "source_key": document["source_key"],
                            "external_id": document["external_id"],
                            "status": event_status,
                            "error": failure_text,
                        }
                    )
                    continue
                if index + 1 < len(documents):
                    time.sleep(
                        float(os.getenv("LEGAL_SOURCE_REQUEST_DELAY_SECONDS", "1"))
                    )
    return results


def retry_due_documents(
    documents: list[dict[str, Any]],
    catalog: dict[str, Any],
    db_url: str | None,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Run one bounded retry tranche from durable source checkpoints."""
    if limit < 1:
        raise ValueError("retry limit must be positive")
    init_schema(db_url)
    due_sources: set[str] = set()
    with connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT source_key
                FROM authority_harvest_checkpoints
                WHERE status IN ('retryable_failure', 'retryable')
                  AND next_retry_at IS NOT NULL AND next_retry_at <= now()
                ORDER BY source_key LIMIT %s
            """,
                [limit],
            )
            due_sources = {row[0] for row in cur.fetchall()}
    if not due_sources:
        return []
    selected = [d for d in documents if d.get("source_key") in due_sources][:limit]
    return sync_documents(selected, catalog, db_url)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch reviewed public-authority manifests"
    )
    parser.add_argument("--manifest")
    parser.add_argument("--catalog")
    parser.add_argument("--source-key", action="append", dest="source_keys")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--preview", action="store_true", help="Fetch and parse without database writes"
    )
    parser.add_argument(
        "--download-dir",
        help="With --preview, retain raw and normalized artifacts plus preview-manifest.json",
    )
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument(
        "--sync", action="store_true", help="Fetch and upsert documents/chunks"
    )
    parser.add_argument("--db-url")
    args = parser.parse_args()
    if args.preview and args.sync:
        parser.error("choose either --preview or --sync")
    if args.download_dir and not args.preview:
        parser.error("--download-dir requires --preview")
    if args.download_dir and (args.limit is None or args.limit < 1):
        parser.error("--download-dir requires a positive --limit for a bounded fetch")

    catalog = load_catalog(args.catalog)
    manifest = load_manifest(args.manifest)
    validate_manifest(manifest, catalog)
    documents = selected_documents(
        manifest,
        args.source_keys,
        args.limit,
        for_sync=args.sync,
    )

    if args.preview:
        print(
            json.dumps(
                preview(
                    documents,
                    delay_seconds=args.delay_seconds,
                    output_dir=args.download_dir,
                ),
                indent=2,
            )
        )
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
