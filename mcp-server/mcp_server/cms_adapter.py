"""Rights-aware CMS Coverage API and CMS manuals/transmittals manifest discovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from io import BytesIO
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import httpx

from .authority_adapter_store import AdapterDocument
from .authority_ingest import html_main_text

COVERAGE_API = "https://api.coverage.cms.gov/v1/data"
REPORT_API = "https://api.coverage.cms.gov/v1/reports"
MANUALS_URL = "https://www.cms.gov/medicare/regulations-guidance/manuals/internet-only-manuals-ioms"
TRANSMITTALS_URL = "https://www.cms.gov/medicare/regulations-guidance/transmittals"
USER_AGENT = os.getenv("LEGAL_SOURCE_USER_AGENT") or "LegalApp-CMSSync/0.1 (+https://github.com/mattpainter701/legalapp)"
# These fields can contain AMA, ADA, AHA or other licensed code descriptions.  Filtering
# is deliberately conservative: an adapter must never obtain a license token itself.
LICENSED_FIELD_PARTS = ("cpt", "cdt", "hcpcs", "code_description", "codedescription", "shortdescription", "longdescription", "ama", "ada", "aha")


def _key_is_licensed(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(part.replace("_", "") in compact for part in LICENSED_FIELD_PARTS)


def public_fields(value: Any) -> Any:
    """Remove license-gated fields recursively, retaining public coverage narrative."""
    if isinstance(value, dict):
        return {key: public_fields(item) for key, item in value.items() if not _key_is_licensed(key)}
    if isinstance(value, list):
        return [public_fields(item) for item in value]
    return value


def _find(payload: dict, *names: str) -> Any:
    wanted = {name.lower() for name in names}
    for key, value in payload.items():
        if key.lower() in wanted:
            return value
    return None


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(filter(None, (_as_text(item) for item in value)))
    if isinstance(value, dict):
        return "\n".join(f"{key}: {_as_text(item)}" for key, item in value.items() if _as_text(item))
    return ""


def coverage_document(entity: str, item: dict, *, retrieved_at: datetime | None = None) -> AdapterDocument:
    public = public_fields(item)
    identifier = str(_find(public, "id", "documentId", "document_id", "lcdId", "ncdId", "articleId") or "").strip()
    if not identifier:
        raise ValueError("CMS coverage item lacks an identifier")
    title = str(_find(public, "title", "documentTitle", "name") or f"CMS {entity} {identifier}")
    revision = _find(public, "revision", "revisionNumber", "version", "versionNumber", "revisionDate", "lastUpdated")
    effective_raw = _find(public, "effectiveDate", "effective_date")
    effective = None
    if isinstance(effective_raw, str):
        try:
            effective = date.fromisoformat(effective_raw[:10])
        except ValueError:
            pass
    authority = "agency_interpretation" if entity.lower() in {"ncd", "lcd"} else "agency_guidance"
    document_type = {"ncd": "national_coverage_determination", "lcd": "local_coverage_determination"}.get(entity.lower(), f"cms_{entity}")
    text = _as_text(public)
    return AdapterDocument(
        source_key="cms:medicare-coverage-api", external_id=f"cms:{entity}:{identifier}",
        document_type=document_type, title=title, citation=f"{entity.upper()} {identifier}",
        jurisdiction="US", authority_tier=authority,
        canonical_url=str(_find(public, "url", "documentUrl") or f"{COVERAGE_API}/{entity}/{identifier}"), text=text,
        effective_date=effective, retrieved_at=retrieved_at, raw_media_type="application/json",
        parser_version="cms-coverage-public-v1",
        metadata={"entity": entity, "stable_id": f"cms:{entity}:{identifier}", "revision": revision,
                  "version_id": str(revision or "unknown"), "public_fields_only": True,
                  "licensed_fields_excluded": True,
                  "payload_sha256": hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()},
    )


def paged_coverage_items(client: httpx.Client, entity: str, *, limit: int | None = None, retries: int = 3) -> Iterable[dict]:
    report = {"ncd": "national-coverage-ncd", "lcd": "local-coverage-final-lcds", "article": "local-coverage-final-lcds"}.get(entity)
    if report is None:
        raise ValueError(f"unsupported CMS coverage entity {entity}")
    url: str | None = f"{REPORT_API}/{report}/"
    count = 0
    while url and (limit is None or count < limit):
        for attempt in range(retries):
            response = client.get(url)
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < retries:
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            break
        payload = response.json()
        items = (payload.get("results") or payload.get("data") or []) if isinstance(payload, dict) else payload
        for item in items:
            if isinstance(item, dict):
                # The NCD detail endpoint is public when identifier/version are supplied.
                # LCD detail can demand license-token data, so report metadata is retained only.
                if entity == "ncd":
                    identifier = item.get("document_id")
                    version = item.get("document_version")
                    detail = client.get(f"{COVERAGE_API}/ncd/", params={"ncdid": identifier, "ncdver": version})
                    detail.raise_for_status()
                    detail_payload = detail.json()
                    item = (detail_payload.get("data") or [item])[0] if isinstance(detail_payload, dict) else item
                yield item
                count += 1
                if limit is not None and count >= limit:
                    return
        next_url = payload.get("next") if isinstance(payload, dict) else None
        url = urljoin(url, next_url) if next_url else None


class _CMSLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._text: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


@dataclass(frozen=True)
class CMSManifestEntry:
    source_key: str
    external_id: str
    title: str
    canonical_url: str
    document_type: str
    page_url: str
    discovered_at: datetime


def fetch_manifest_document(entry: CMSManifestEntry, *, client: httpx.Client, max_bytes: int = 32 * 1024 * 1024) -> AdapterDocument:
    """Fetch an allowlisted CMS artifact only; never accepts arbitrary URLs or tokens."""
    with client.stream("GET", entry.canonical_url) as response:
        response.raise_for_status()
        announced = response.headers.get("content-length")
        if announced and int(announced) > max_bytes:
            raise RuntimeError(f"CMS artifact exceeds {max_bytes} byte bound")
        raw = bytearray()
        for block in response.iter_bytes():
            raw.extend(block)
            if len(raw) > max_bytes:
                raise RuntimeError(f"CMS artifact exceeded {max_bytes} byte bound")
    media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if media_type == "application/pdf" or entry.canonical_url.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # explicit dependency instead of silently storing opaque PDF
            raise RuntimeError("pypdf is required for CMS PDF ingestion") from exc
        text = "\n".join((page.extract_text() or "") for page in PdfReader(BytesIO(bytes(raw))).pages)
    else:
        text = html_main_text(bytes(raw).decode("utf-8", errors="replace"))
    if len(text.strip()) < 100:
        raise RuntimeError("CMS artifact produced too little readable text")
    revision = response.headers.get("etag") or response.headers.get("last-modified")
    return AdapterDocument(
        source_key=entry.source_key, external_id=entry.external_id, document_type=entry.document_type,
        title=entry.title, citation=None, jurisdiction="US", authority_tier="agency_guidance",
        canonical_url=entry.canonical_url, text=text, retrieved_at=datetime.now(timezone.utc),
        raw_media_type=media_type or "application/octet-stream", parser_version="cms-artifact-v1",
        metadata={"discovery_page": entry.page_url, "revision": revision, "version_id": revision or "unknown",
                  "artifact_sha256": hashlib.sha256(bytes(raw)).hexdigest(), "public_fields_only": True},
    )


def discover_cms_manifest(html: str, *, page_url: str, kind: str, discovered_at: datetime | None = None) -> list[CMSManifestEntry]:
    parser = _CMSLinkParser()
    parser.feed(html)
    discovered_at = discovered_at or datetime.now(timezone.utc)
    source_key = "cms:internet-only-manuals" if kind == "manual" else "cms:transmittals"
    document_type = "cms_manual_artifact" if kind == "manual" else "cms_transmittal_artifact"
    entries: dict[str, CMSManifestEntry] = {}
    for href, label in parser.links:
        url = urljoin(page_url, href)
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc.endswith("cms.gov"):
            continue
        path = parsed.path.lower().rstrip("/")
        normalized_label = label.lower()
        is_pdf = path.endswith(".pdf")
        if kind == "manual":
            is_item_page = "/internet-only-manuals-ioms-items/" in path
            is_artifact = is_pdf and (
                "/guidance/manuals/downloads/" in path
                or "manual" in normalized_label
                or re.search(r"\bpub(?:lication)?\.?\s*\d", normalized_label) is not None
            )
        elif kind == "transmittal":
            is_item_page = re.search(
                r"/transmittals/(?:\d{4}-transmittals(?:/r[0-9a-z]+)?|r[0-9a-z]+)$",
                path,
            ) is not None
            is_artifact = is_pdf and (
                re.search(r"(?:^|/)r\d+[a-z0-9_-]*\.pdf$", path) is not None
                or re.search(r"\br\d+[a-z]+\b", normalized_label) is not None
            )
        else:
            raise ValueError(f"unsupported CMS manifest kind {kind!r}")
        if not (is_item_page or is_artifact):
            continue
        external_id = f"cms-{kind}:{hashlib.sha256(url.encode()).hexdigest()[:24]}"
        entries[url] = CMSManifestEntry(source_key, external_id, label or url.rsplit("/", 1)[-1], url, document_type, page_url, discovered_at)
    return list(entries.values())


def discover_cms_artifacts(
    client: httpx.Client,
    *,
    page_url: str,
    kind: str,
    limit: int | None = None,
    max_pages: int = 500,
) -> list[CMSManifestEntry]:
    """Traverse only CMS manual/transmittal index pages to final PDF artifacts."""
    queue: list[tuple[str, int]] = [(page_url, 0)]
    seen_pages: set[str] = set()
    artifacts: dict[str, CMSManifestEntry] = {}
    while queue and len(seen_pages) < max_pages and (limit is None or len(artifacts) < limit):
        current_url, depth = queue.pop(0)
        if current_url in seen_pages or depth > 3:
            continue
        seen_pages.add(current_url)
        response = client.get(current_url)
        response.raise_for_status()
        for entry in discover_cms_manifest(response.text, page_url=current_url, kind=kind):
            if urlparse(entry.canonical_url).path.lower().endswith(".pdf"):
                artifacts[entry.canonical_url] = entry
                if limit is not None and len(artifacts) >= limit:
                    break
            elif entry.canonical_url not in seen_pages:
                queue.append((entry.canonical_url, depth + 1))
    return list(artifacts.values())
