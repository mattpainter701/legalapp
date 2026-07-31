"""Bounded adapters for official IRS bulletins and estate/fiduciary product pages."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from io import BytesIO
from typing import Iterable
from urllib.parse import urljoin, urlparse

import httpx

from .authority_adapter_store import AdapterDocument
from .authority_ingest import html_main_text

IRB_INDEX_URL = "https://www.irs.gov/internal-revenue-bulletins"
ESTATE_FORMS_INDEX_URL = "https://www.irs.gov/businesses/small-businesses-self-employed/forms-and-publications-estate-and-gift-tax"
USER_AGENT = os.getenv("LEGAL_SOURCE_USER_AGENT") or "LegalApp-IRSSync/0.1 (+https://github.com/mattpainter701/legalapp; official legal-data retrieval)"
MAX_BYTES = 32 * 1024 * 1024
PRODUCTS = {"56", "2848", "4506", "706", "709", "8971", "1041", "4768", "559", "230"}
IRB_LINK = re.compile(r"/irb/(?P<issue>20\d{2}-\d{2})_IRB/?$", re.I)
IRB_PDF_LINK = re.compile(r"/pub/irs-irbs/irb(?P<year>\d{2})-(?P<number>\d{2})\.pdf$", re.I)
IRB_ITEM = re.compile(r"(?m)^(?P<kind>Rev\.\s*Rul\.|Rev\.\s*Proc\.|Notice|T\.D\.)\s*(?P<number>20\d{2}-\d+|\d{4,6})\b")


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.links: list[tuple[str, str]] = []; self.href = None; self.words: list[str] = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a": self.href = dict(attrs).get("href"); self.words = []
    def handle_data(self, data):
        if self.href: self.words.append(data)
    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href:
            self.links.append((self.href, " ".join("".join(self.words).split()))); self.href = None


@dataclass(frozen=True)
class IRSArtifact:
    source_key: str
    external_id: str
    title: str
    canonical_url: str
    document_type: str
    authority_tier: str
    stable_id: str
    version_id: str


def _irs_url(base: str, href: str) -> str | None:
    url = urljoin(base, href); parsed = urlparse(url)
    return url if parsed.scheme == "https" and parsed.netloc.lower().endswith("irs.gov") else None


def fetch_bounded(
    client: httpx.Client, url: str, *, max_bytes: int = MAX_BYTES
) -> tuple[bytes, str, dict[str, str]]:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        announced = response.headers.get("content-length")
        if announced and int(announced) > max_bytes:
            raise RuntimeError("IRS artifact exceeds byte bound")
        raw = bytearray()
        for block in response.iter_bytes():
            raw.extend(block)
            if len(raw) > max_bytes:
                raise RuntimeError("IRS artifact exceeded byte bound")
        media = response.headers.get("content-type", "").split(";", 1)[0].lower()
        return bytes(raw), media, dict(response.headers)


def discover_irb_issues(html: str, *, base_url: str = IRB_INDEX_URL) -> list[IRSArtifact]:
    parser = _Links(); parser.feed(html); found = {}
    for href, label in parser.links:
        url = _irs_url(base_url, href)
        match = IRB_LINK.search(urlparse(url).path) if url else None
        pdf_match = IRB_PDF_LINK.search(urlparse(url).path) if url else None
        if pdf_match:
            match_issue = f"20{pdf_match.group('year')}-{pdf_match.group('number')}"
        else:
            match_issue = match.group("issue") if match else None
        if match_issue:
            issue = match_issue
            found[issue] = IRSArtifact("irs:internal-revenue-bulletin", f"irs:irb:{issue}", label or f"Internal Revenue Bulletin {issue}", url, "internal_revenue_bulletin", "agency_interpretation", f"irs:irb:{issue}", issue)
    return [found[key] for key in sorted(found, reverse=True)]


def irb_documents(issue: IRSArtifact, html: str, *, retrieved_at: datetime | None = None) -> list[AdapterDocument]:
    text = html_main_text(html) if "<" in html else "\n".join(line.strip() for line in html.splitlines() if line.strip())
    matches = list(IRB_ITEM.finditer(text)); retrieved_at = retrieved_at or datetime.now(timezone.utc)
    artifact_hash = hashlib.sha256(text.encode()).hexdigest()
    if not matches:
        matches = []
    result: list[AdapterDocument] = []
    for index, match in enumerate(matches):
        body = text[match.start(): matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
        if len(body) < 40: continue
        kind = re.sub(r"\s+", " ", match.group("kind").replace(".", "")).strip().lower().replace(" ", "-")
        number = match.group("number")
        stable_id = f"irs:{kind}:{number}"; external_id = f"irs:irb:{issue.version_id}:{kind}:{number}"
        result.append(AdapterDocument(issue.source_key, external_id, {"rev-rul": "revenue_ruling", "rev-proc": "revenue_procedure", "notice": "irs_notice", "td": "treasury_decision"}.get(kind, "irs_guidance"), body.split("\n", 1)[0][:300], f"{match.group('kind')} {number}", "US", "agency_interpretation", issue.canonical_url, body, retrieved_at=retrieved_at, raw_media_type="text/html", parser_version="irs-irb-item-v1", metadata={"issue": issue.version_id, "stable_id": stable_id, "version_id": issue.version_id, "artifact_sha256": artifact_hash, "item_kind": kind, "item_number": number}))
    if result:
        # PDF tables of contents repeat item headings. Preserve the longest body for
        # each stable item/version identity rather than creating duplicate upserts.
        deduped = {}
        for document in result:
            current = deduped.get(document.external_id)
            if current is None or len(document.text) > len(current.text):
                deduped[document.external_id] = document
        return list(deduped.values())
    return [AdapterDocument(issue.source_key, issue.external_id, issue.document_type, issue.title, f"IRB {issue.version_id}", "US", issue.authority_tier, issue.canonical_url, text, retrieved_at=retrieved_at, raw_media_type="text/html", parser_version="irs-irb-issue-v1", metadata={"stable_id": issue.stable_id, "version_id": issue.version_id, "artifact_sha256": artifact_hash})]


def _product_number(label: str, url: str) -> str | None:
    match = re.search(r"(?:form|publication|pub\.?|instructions?\s+for\s+form)\s*(\d{2,4})\b", label, re.I)
    if match and match.group(1) in PRODUCTS: return match.group(1)
    match = re.search(r"(?:f|i|p)(\d{2,4})(?:[-.]|$)", urlparse(url).path.rsplit("/", 1)[-1], re.I)
    return match.group(1) if match and match.group(1) in PRODUCTS else None


def discover_estate_products(html: str, *, base_url: str = ESTATE_FORMS_INDEX_URL) -> list[IRSArtifact]:
    parser = _Links(); parser.feed(html); found = {}
    for href, label in parser.links:
        url = _irs_url(base_url, href)
        if not url: continue
        number = _product_number(label, url)
        path = urlparse(url).path.lower()
        if not number or not (path.startswith("/forms-pubs/") or path.startswith("/pub/irs-pdf/") or "/forms/" in path): continue
        kind = "instructions" if "instruction" in label.lower() or "/i" in path else ("publication" if "publication" in label.lower() or "/p" in path else "form")
        stable_id = f"irs:{kind}:{number}"; found[url] = IRSArtifact("irs:estate-gift-forms", stable_id, label or f"IRS {kind} {number}", url, f"irs_{kind}", "official_form", stable_id, "pending")
    return list(found.values())


def fetch_product(artifact: IRSArtifact, *, client: httpx.Client, max_bytes: int = MAX_BYTES) -> AdapterDocument:
    data, media, headers = fetch_bounded(client, artifact.canonical_url, max_bytes=max_bytes)
    if media == "application/pdf" or artifact.canonical_url.lower().endswith(".pdf"):
        from pypdf import PdfReader
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)
    else: text = html_main_text(data.decode("utf-8", errors="replace"))
    if len(text.strip()) < 80: raise RuntimeError("IRS product produced too little readable text")
    revision = re.search(r"\b(?:Rev\.?|Revision)\s*[:.]?\s*([0-9-]{4,10})", text, re.I)
    version = revision.group(1) if revision else (headers.get("etag") or hashlib.sha256(data).hexdigest()[:16])
    safe_version = re.sub(r"[^0-9A-Za-z._-]+", "-", str(version)).strip("-")
    return AdapterDocument(artifact.source_key, f"{artifact.stable_id}:{safe_version}", artifact.document_type, artifact.title, artifact.stable_id.replace("irs:", "IRS ").replace(":", " "), "US", artifact.authority_tier, artifact.canonical_url, text, retrieved_at=datetime.now(timezone.utc), raw_media_type=media or "application/octet-stream", parser_version="irs-product-v1", metadata={"stable_id": artifact.stable_id, "version_id": version, "artifact_sha256": hashlib.sha256(data).hexdigest(), "etag": headers.get("etag"), "last_modified": headers.get("last-modified")})
