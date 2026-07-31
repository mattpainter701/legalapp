"""Official U.S. Code USLM release-point discovery and bounded download scaffold."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Iterable, Iterator
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import httpx

from .database import connect
from .loader import chunk_text, init_schema
from .source_catalog import load_catalog, seed_catalog

USCODE_DOWNLOAD_PAGE = "https://uscode.house.gov/download/download.shtml"
DEFAULT_TITLES = (11, 15, 26, 28, 29, 31, 42)
DEFAULT_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_ZIP_ENTRIES = 32
DEFAULT_MAX_COMPRESSION_RATIO = 100
SOURCE_KEY = "federal:us-code"
PARSER_VERSION = "uslm-section-v1"
USER_AGENT = os.getenv("LEGAL_SOURCE_USER_AGENT") or (
    "LegalApp-USCodeSync/0.1 "
    "(+https://github.com/mattpainter701/legalapp; official legal-data retrieval)"
)
_XML_TITLE_RE = re.compile(
    r"^releasepoints/us/pl/(?P<congress>\d+)/(?P<law>\d+)/"
    r"xml_usc(?P<title>\d{2})@(?P<release>\d+-\d+)\.zip$"
)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


@dataclass(frozen=True)
class USCodeArtifact:
    title: int
    release_point: str
    congress: int
    public_law_number: int
    url: str

    @property
    def stable_prefix(self) -> str:
        return f"usc:{self.release_point}:title-{self.title}"


@dataclass(frozen=True)
class DownloadedArtifact:
    artifact: USCodeArtifact
    content: bytes
    sha256: str


@dataclass(frozen=True)
class USCodeSection:
    """A single, versioned U.S. Code section ready for authority ingestion."""

    artifact: USCodeArtifact
    section: str
    heading: str | None
    text: str
    external_id: str
    citation: str

    @property
    def title(self) -> str:
        suffix = f" — {self.heading}" if self.heading else ""
        return f"{self.citation}{suffix}"

    def document(self, *, artifact_sha256: str) -> dict[str, Any]:
        """Return the same document-shaped contract used by authority ingestion."""
        return {
            "source_key": SOURCE_KEY,
            "external_id": self.external_id,
            "document_type": "statute_section",
            "title": self.title,
            "citation": self.citation,
            "jurisdiction": "US",
            "authority_tier": "binding_primary",
            "document_status": "current",
            "publication_date": None,
            "effective_date": None,
            "canonical_url": self.artifact.url,
            "parser": PARSER_VERSION,
            "practice_areas": ["federal", "statutes"],
            "metadata": {
                "title_number": self.artifact.title,
                "section_number": self.section,
                "release_point": self.artifact.release_point,
                "public_law": f"{self.artifact.congress}-{self.artifact.public_law_number}",
                "artifact_sha256": artifact_sha256,
                "artifact_url": self.artifact.url,
                "native_id": self.external_id,
            },
        }


def _release_sort_key(release_point: str) -> tuple[int, int]:
    congress, public_law = release_point.split("-", 1)
    return int(congress), int(public_law)


def discover_artifacts(
    html: str,
    titles: Iterable[int] = DEFAULT_TITLES,
    *,
    base_url: str = USCODE_DOWNLOAD_PAGE,
) -> list[USCodeArtifact]:
    wanted = {int(title) for title in titles}
    if not wanted or any(title <= 0 for title in wanted):
        raise ValueError("at least one positive U.S. Code title is required")
    parser = _LinkParser()
    parser.feed(html)
    artifacts: dict[tuple[int, str], USCodeArtifact] = {}
    for href in parser.links:
        match = _XML_TITLE_RE.match(href.lstrip("/"))
        if not match:
            continue
        title = int(match.group("title"))
        release_point = match.group("release")
        congress = int(match.group("congress"))
        public_law = int(match.group("law"))
        if title not in wanted or release_point != f"{congress}-{public_law}":
            continue
        artifact = USCodeArtifact(
            title=title,
            release_point=release_point,
            congress=congress,
            public_law_number=public_law,
            url=urljoin(base_url, href),
        )
        artifacts[(title, release_point)] = artifact

    if not artifacts:
        raise RuntimeError("no matching official U.S. Code USLM title artifacts discovered")
    newest_release = max(
        (artifact.release_point for artifact in artifacts.values()),
        key=_release_sort_key,
    )
    discovered = [
        artifact
        for artifact in artifacts.values()
        if artifact.release_point == newest_release
    ]
    missing = wanted - {artifact.title for artifact in discovered}
    if missing:
        raise RuntimeError(
            "latest U.S. Code release is missing requested title(s): "
            + ", ".join(str(title) for title in sorted(missing))
        )
    return sorted(discovered, key=lambda artifact: artifact.title)


def fetch_download_page(client: httpx.Client | None = None) -> str:
    owns_client = client is None
    client = client or httpx.Client(
        timeout=45.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    try:
        response = client.get(USCODE_DOWNLOAD_PAGE)
        response.raise_for_status()
        return response.text
    finally:
        if owns_client:
            client.close()


def download_artifact(
    artifact: USCodeArtifact,
    *,
    client: httpx.Client | None = None,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> DownloadedArtifact:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    owns_client = client is None
    client = client or httpx.Client(
        timeout=120.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/zip"},
    )
    try:
        with client.stream("GET", artifact.url) as response:
            response.raise_for_status()
            announced = response.headers.get("content-length")
            if announced and int(announced) > max_bytes:
                raise RuntimeError(
                    f"U.S. Code artifact exceeds limit: {announced} > {max_bytes}"
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise RuntimeError(
                        f"U.S. Code artifact exceeded {max_bytes} bytes while downloading"
                    )
        content = bytes(body)
        if not content.startswith(b"PK"):
            raise RuntimeError("U.S. Code artifact is not a ZIP file")
        return DownloadedArtifact(
            artifact=artifact,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
        )
    finally:
        if owns_client:
            client.close()


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _normalized_text(element: ET.Element) -> str:
    return " ".join(" ".join(element.itertext()).split())


def _section_number(section: ET.Element) -> str | None:
    """Extract the USLM section number without relying on a fixed namespace/schema rev."""
    for key in ("identifier", "id", "value"):
        value = section.attrib.get(key, "")
        match = re.search(r"(?:/s|section[-:/])(?P<number>[0-9A-Za-z.-]+)$", value)
        if match:
            return match.group("number")
    for child in section:
        if _local_name(child.tag) in {"num", "sectionNumber"}:
            value = child.attrib.get("value") or _normalized_text(child)
            value = value.replace("§", "").strip()
            match = re.search(r"([0-9][0-9A-Za-z.-]*)", value)
            if match:
                return match.group(1)
    return None


def _direct_child_text(section: ET.Element, names: set[str]) -> str | None:
    for child in section:
        if _local_name(child.tag) in names:
            value = _normalized_text(child)
            if value:
                return value
    return None


def _safe_xml_entry(
    downloaded: DownloadedArtifact,
    *,
    max_uncompressed_bytes: int,
    max_entries: int,
    max_compression_ratio: int,
) -> bytes:
    """Return the sole XML payload after rejecting zip-bomb and path abuse patterns."""
    if max_uncompressed_bytes <= 0 or max_entries <= 0 or max_compression_ratio <= 0:
        raise ValueError("ZIP validation limits must be positive")
    try:
        archive = zipfile.ZipFile(BytesIO(downloaded.content))
    except zipfile.BadZipFile as exc:
        raise RuntimeError("U.S. Code artifact is not a valid ZIP archive") from exc
    with archive:
        entries = archive.infolist()
        if not entries or len(entries) > max_entries:
            raise RuntimeError("U.S. Code ZIP has an unsafe number of entries")
        xml_entries = []
        total_uncompressed = 0
        for entry in entries:
            path = entry.filename.replace("\\", "/")
            if path.startswith("/") or ".." in path.split("/") or entry.is_dir():
                raise RuntimeError("U.S. Code ZIP contains an unsafe entry path")
            total_uncompressed += entry.file_size
            if total_uncompressed > max_uncompressed_bytes:
                raise RuntimeError("U.S. Code ZIP exceeds uncompressed size limit")
            compressed = max(entry.compress_size, 1)
            if entry.file_size / compressed > max_compression_ratio:
                raise RuntimeError("U.S. Code ZIP exceeds compression ratio limit")
            if path.lower().endswith(".xml"):
                xml_entries.append(entry)
        if len(xml_entries) != 1:
            raise RuntimeError("U.S. Code ZIP must contain exactly one XML document")
        entry = xml_entries[0]
        if f"usc{downloaded.artifact.title:02d}" not in entry.filename.lower():
            raise RuntimeError("U.S. Code XML entry does not match requested title")
        with archive.open(entry) as source:
            payload = source.read(max_uncompressed_bytes + 1)
        if len(payload) > max_uncompressed_bytes:
            raise RuntimeError("U.S. Code XML exceeds uncompressed size limit")
        return payload


def parse_uslm_sections(
    downloaded: DownloadedArtifact,
    *,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_entries: int = DEFAULT_MAX_ZIP_ENTRIES,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
) -> list[USCodeSection]:
    """Validate an official title archive and emit non-empty USLM sections.

    USLM uses namespaces and has evolved over time, so this deliberately matches
    local element names while retaining the original artifact/release metadata.
    """
    payload = _safe_xml_entry(
        downloaded,
        max_uncompressed_bytes=max_uncompressed_bytes,
        max_entries=max_entries,
        max_compression_ratio=max_compression_ratio,
    )
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise RuntimeError("U.S. Code XML may not contain DTD or entity declarations")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError("U.S. Code XML could not be parsed") from exc

    emitted: dict[str, USCodeSection] = {}
    for element in root.iter():
        if _local_name(element.tag) != "section":
            continue
        number = _section_number(element)
        text = _normalized_text(element)
        if not number or not text:
            continue
        heading = _direct_child_text(element, {"heading", "headingText"})
        external_id = f"{downloaded.artifact.stable_prefix}:section-{number}"
        candidate = USCodeSection(
            artifact=downloaded.artifact,
            section=number,
            heading=heading,
            text=text,
            external_id=external_id,
            citation=f"{downloaded.artifact.title} U.S.C. § {number}",
        )
        # Official title packages can include a short navigation/repeal node
        # before the complete node for the same statutory section. Section
        # number remains the identity; deterministically retain the fullest
        # official representation rather than duplicate search citations.
        existing = emitted.get(external_id)
        if existing is None or len(candidate.text) > len(existing.text):
            emitted[external_id] = candidate
    if not emitted:
        raise RuntimeError("U.S. Code XML contained no parseable sections")
    return sorted(emitted.values(), key=lambda item: item.section)


def iter_sections(downloaded: DownloadedArtifact, limit: int | None = None) -> Iterator[USCodeSection]:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive when supplied")
    sections = parse_uslm_sections(downloaded)
    yield from sections[:limit]


def _upsert_section(conn: Any, section: USCodeSection, artifact_sha256: str) -> dict[str, Any]:
    """Idempotently persist a section and replace chunks only if its text changed."""
    document = section.document(artifact_sha256=artifact_sha256)
    content_hash = hashlib.sha256(section.text.encode("utf-8")).hexdigest()
    retrieved_at = datetime.now(timezone.utc)
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, content_hash FROM legal_documents WHERE source_key = %s AND external_id = %s",
            [SOURCE_KEY, section.external_id],
        )
        existing = cursor.fetchone()
        changed = existing is None or existing[1] != content_hash
        cursor.execute(
            """
            INSERT INTO legal_documents (
                source_key, external_id, document_type, title, citation, jurisdiction,
                authority_tier, document_status, publication_date, effective_date,
                canonical_url, retrieved_at, content_hash, raw_media_type,
                parser_version, text_content, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (source_key, external_id) DO UPDATE SET
                title = EXCLUDED.title, citation = EXCLUDED.citation,
                document_status = EXCLUDED.document_status, canonical_url = EXCLUDED.canonical_url,
                retrieved_at = EXCLUDED.retrieved_at, content_hash = EXCLUDED.content_hash,
                raw_media_type = EXCLUDED.raw_media_type, parser_version = EXCLUDED.parser_version,
                text_content = EXCLUDED.text_content,
                metadata = legal_documents.metadata || EXCLUDED.metadata, updated_at = now()
            RETURNING id
            """,
            [SOURCE_KEY, section.external_id, document["document_type"], document["title"],
             document["citation"], document["jurisdiction"], document["authority_tier"],
             document["document_status"], None, None, document["canonical_url"], retrieved_at,
             content_hash, "application/xml", PARSER_VERSION, section.text,
             json.dumps(document["metadata"])],
        )
        document_id = cursor.fetchone()[0]
        chunk_count = 0
        if changed:
            cursor.execute("DELETE FROM legal_document_chunks WHERE document_id = %s", [document_id])
            for index, content in enumerate(chunk_text(section.text)):
                cursor.execute(
                    """INSERT INTO legal_document_chunks
                    (document_id, chunk_index, content, content_hash, embedding, embedding_version, metadata)
                    VALUES (%s, %s, %s, %s, NULL, 0, %s::jsonb)""",
                    [document_id, index, content, hashlib.sha256(content.encode("utf-8")).hexdigest(),
                     json.dumps({"citation": section.citation, "section_number": section.section})],
                )
                chunk_count += 1
    return {"external_id": section.external_id, "changed": changed, "chunks_created": chunk_count}


def _checkpoint(conn: Any, artifact: USCodeArtifact, *, rows: int, chunks: int, artifact_sha256: str, status: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO source_sync_states
            (source_key, partition_key, checkpoint_at, status, last_attempted_at,
             last_successful_sync_at, rows_processed, chunks_created, last_error, metadata)
            VALUES (%s, %s, now(), %s, now(), CASE WHEN %s = 'complete' THEN now() ELSE NULL END,
                    %s, %s, NULL, %s::jsonb)
            ON CONFLICT (source_key, partition_key) DO UPDATE SET
              checkpoint_at = EXCLUDED.checkpoint_at, status = EXCLUDED.status,
              last_attempted_at = EXCLUDED.last_attempted_at,
              last_successful_sync_at = EXCLUDED.last_successful_sync_at,
              rows_processed = source_sync_states.rows_processed + EXCLUDED.rows_processed,
              chunks_created = source_sync_states.chunks_created + EXCLUDED.chunks_created,
              last_error = NULL, metadata = EXCLUDED.metadata, updated_at = now()""",
            [SOURCE_KEY, f"title-{artifact.title}", status, status, rows, chunks,
             json.dumps({"release_point": artifact.release_point, "artifact_sha256": artifact_sha256,
                         "artifact_url": artifact.url, "parser_version": PARSER_VERSION})],
        )


def sync_downloaded_artifact(conn: Any, downloaded: DownloadedArtifact, *, limit: int | None = None) -> dict[str, Any]:
    """Upsert a title incrementally, checkpointing after every committed section."""
    rows = chunks = 0
    for section in iter_sections(downloaded, limit=limit):
        result = _upsert_section(conn, section, downloaded.sha256)
        rows += 1
        chunks += result["chunks_created"]
        _checkpoint(conn, downloaded.artifact, rows=1, chunks=result["chunks_created"], artifact_sha256=downloaded.sha256, status="running")
        conn.commit()
    _checkpoint(conn, downloaded.artifact, rows=0, chunks=0, artifact_sha256=downloaded.sha256, status="complete")
    with conn.cursor() as cursor:
        cursor.execute(
            """UPDATE legal_documents
               SET document_status = 'superseded', termination_date = CURRENT_DATE,
                   updated_at = now()
               WHERE source_key = %s
                 AND metadata->>'title_number' = %s
                 AND metadata->>'release_point' <> %s
                 AND document_status = 'current'""",
            [SOURCE_KEY, str(downloaded.artifact.title), downloaded.artifact.release_point],
        )
        cursor.execute(
            """UPDATE legal_sources SET last_attempted_at = now(), last_successful_sync_at = now(),
            current_error = NULL, item_count = (SELECT COUNT(*) FROM legal_documents WHERE source_key = %s),
            chunk_count = (SELECT COUNT(*) FROM legal_document_chunks c JOIN legal_documents d ON d.id = c.document_id WHERE d.source_key = %s),
            updated_at = now() WHERE source_key = %s""", [SOURCE_KEY, SOURCE_KEY, SOURCE_KEY]
        )
    conn.commit()
    return {"title": downloaded.artifact.title, "release_point": downloaded.artifact.release_point,
            "rows_processed": rows, "chunks_created": chunks, "artifact_sha256": downloaded.sha256}


def sync_official_titles(
    *, titles: Iterable[int] = DEFAULT_TITLES, db_url: str | None = None, limit: int | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Discover, download, and resumably upsert the requested official U.S. Code titles."""
    artifacts = discover_artifacts(fetch_download_page(client), titles)
    init_schema(db_url)
    with connect(db_url) as conn:
        seed_catalog(conn, load_catalog())
        results = []
        for artifact in artifacts:
            downloaded = download_artifact(artifact, client=client)
            results.append(sync_downloaded_artifact(conn, downloaded, limit=limit))
        return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover official U.S. Code USLM release-point artifacts"
    )
    parser.add_argument("--title", type=int, action="append", dest="titles")
    parser.add_argument("--limit", type=int, help="Maximum sections per title")
    parser.add_argument("--dry-run", action="store_true", help="Download and parse without database writes")
    parser.add_argument("--sync", action="store_true", help="Download and upsert parsed sections")
    parser.add_argument("--db-url")
    args = parser.parse_args()
    if args.dry_run and args.sync:
        parser.error("choose either --dry-run or --sync")
    artifacts = discover_artifacts(fetch_download_page(), args.titles or DEFAULT_TITLES)
    if args.dry_run:
        preview = []
        for artifact in artifacts:
            downloaded = download_artifact(artifact)
            sections = list(iter_sections(downloaded, args.limit))
            preview.append({"title": artifact.title, "release_point": artifact.release_point,
                            "sections": len(sections), "artifact_sha256": downloaded.sha256,
                            "first_external_id": sections[0].external_id if sections else None})
        print(json.dumps(preview, indent=2))
        return
    if args.sync:
        print(json.dumps(sync_official_titles(titles=args.titles or DEFAULT_TITLES, db_url=args.db_url, limit=args.limit), indent=2))
        return
    print(
        json.dumps(
            [
                {
                    "title": artifact.title,
                    "release_point": artifact.release_point,
                    "stable_prefix": artifact.stable_prefix,
                    "url": artifact.url,
                }
                for artifact in artifacts
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
