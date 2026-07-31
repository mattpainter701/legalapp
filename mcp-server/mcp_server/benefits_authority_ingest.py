"""Conservative ingestion for Medicaid.gov and SSA benefits authority.

There is no approved bulk crawler here.  The only automated inputs are a tiny official
allowlist or an operator-reviewed manifest.  SPA/waiver search results and state manuals
remain manifest-only until a stable, documented feed and crawl permission exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .authority_adapter_store import AdapterDocument, refresh_source_status, upsert_adapter_document
from .authority_ingest import html_main_text
from .database import connect
from .loader import init_schema
from .source_catalog import load_catalog, seed_catalog

USER_AGENT = os.getenv("LEGAL_SOURCE_USER_AGENT") or "LegalApp-BenefitsAuthoritySync/0.1 (+https://github.com/mattpainter701/legalapp)"
MAX_BYTES = 24 * 1024 * 1024
DEFAULT_DELAY_SECONDS = 1.0
ALLOWED_HOSTS = {"www.medicaid.gov", "medicaid.gov", "secure.ssa.gov", "www.ssa.gov", "ssa.gov"}
DEFAULT_DOCUMENTS = (
    {"source_key": "cms:medicaid-estate-recovery", "external_id": "medicaid-estate-recovery-overview",
     "document_type": "medicaid_estate_recovery_guidance", "title": "Medicaid Estate Recovery",
     "canonical_url": "https://www.medicaid.gov/medicaid/eligibility-policy/estate-recovery",
     "jurisdiction": "US", "authority_tier": "agency_guidance", "label": "agency_guidance"},
    {"source_key": "medicaid:spa-waivers", "external_id": "medicaid-spa-nd-15-0004",
     "document_type": "medicaid_state_plan_amendment", "title": "North Dakota SPA ND-15-0004",
     "canonical_url": "https://www.medicaid.gov/medicaid-spa/2019-12-08/21886",
     "jurisdiction": "ND", "authority_tier": "agency_interpretation", "label": "cms_approved_state_plan_amendment"},
)


@dataclass(frozen=True)
class BenefitsManifestDocument:
    source_key: str; external_id: str; document_type: str; title: str; canonical_url: str
    jurisdiction: str; authority_tier: str; label: str


def load_reviewed_manifest(path: str | Path | None) -> list[BenefitsManifestDocument]:
    raw = list(DEFAULT_DOCUMENTS)
    if path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        raw.extend(payload.get("documents", payload))
    documents = []
    for item in raw:
        required = {"source_key", "external_id", "document_type", "title", "canonical_url", "jurisdiction", "authority_tier", "label"}
        if missing := required - item.keys():
            raise ValueError(f"benefits manifest missing {sorted(missing)}")
        host = urlparse(item["canonical_url"]).hostname or ""
        if urlparse(item["canonical_url"]).scheme != "https" or host not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved benefits authority host: {host}")
        documents.append(BenefitsManifestDocument(**{key: item[key] for key in required}))
    return documents


def _extract(raw: bytes, media_type: str) -> str:
    if media_type == "application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("pypdf is required to ingest a reviewed PDF") from exc
        return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(raw)).pages)
    return html_main_text(raw.decode("utf-8", errors="replace"))


def fetch_document(document: BenefitsManifestDocument, *, client: httpx.Client, max_bytes: int = MAX_BYTES) -> AdapterDocument:
    with client.stream("GET", document.canonical_url) as response:
        response.raise_for_status()
        length = response.headers.get("content-length")
        if length and int(length) > max_bytes:
            raise RuntimeError(f"authority artifact exceeds {max_bytes} bytes")
        raw = bytearray()
        for part in response.iter_bytes():
            raw.extend(part)
            if len(raw) > max_bytes:
                raise RuntimeError(f"authority artifact exceeded {max_bytes} bytes")
    media_type = response.headers.get("content-type", "text/html").split(";", 1)[0].lower()
    if document.canonical_url.lower().endswith(".pdf"):
        media_type = "application/pdf"
    text = _extract(bytes(raw), media_type).strip()
    if len(text) < 100:
        raise RuntimeError(f"{document.external_id} produced too little readable text")
    modified = None
    if response.headers.get("last-modified"):
        try: modified = parsedate_to_datetime(response.headers["last-modified"])
        except (TypeError, ValueError): pass
    revision = response.headers.get("etag") or response.headers.get("last-modified")
    return AdapterDocument(
        source_key=document.source_key, external_id=document.external_id, document_type=document.document_type,
        title=document.title, citation=None, jurisdiction=document.jurisdiction, authority_tier=document.authority_tier,
        canonical_url=document.canonical_url, text=text, source_modified_at=modified, retrieved_at=datetime.now(timezone.utc),
        raw_media_type=media_type, parser_version="benefits-reviewed-v1",
        metadata={"authority_label": document.label, "stable_id": f"{document.source_key}:{document.external_id}",
                  "version_id": revision or hashlib.sha256(bytes(raw)).hexdigest(), "etag": response.headers.get("etag"),
                  "artifact_sha256": hashlib.sha256(bytes(raw)).hexdigest(), "reviewed_manifest": True},
    )


def _checkpoint(directory: Path, key: str, document: AdapterDocument) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    safe = hashlib.sha256(key.encode()).hexdigest()[:16]
    (directory / f"benefits-{safe}.json").write_text(json.dumps({"external_id": document.external_id, "version_id": document.metadata["version_id"], "updated_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or sync reviewed Medicaid.gov/SSA benefits authority")
    parser.add_argument("--manifest", help="optional reviewed JSON document list; arbitrary hosts are rejected")
    parser.add_argument("--source-key", action="append", dest="source_keys")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--checkpoint-dir", default=".legalapp-checkpoints")
    parser.add_argument("--db-url")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    documents = load_reviewed_manifest(args.manifest)
    if args.source_keys:
        documents = [doc for doc in documents if doc.source_key in set(args.source_keys)]
    documents = documents[:args.limit] if args.limit is not None else documents
    if not documents: parser.error("no approved documents selected")
    if args.sync:
        init_schema(args.db_url)
        with connect(args.db_url) as conn:
            seed_catalog(conn, load_catalog()); conn.commit()
    output: list[dict[str, Any]] = []
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "text/html, application/pdf"}) as client:
        fetched = []
        for position, document in enumerate(documents):
            authority = fetch_document(document, client=client)
            fetched.append(authority)
            output.append({"external_id": authority.external_id, "source_key": authority.source_key, "characters": len(authority.text), "version_id": authority.metadata["version_id"], "preview": args.preview})
            if position + 1 < len(documents): time.sleep(DEFAULT_DELAY_SECONDS)
    if args.sync:
        with connect(args.db_url) as conn:
            for authority in fetched:
                upsert_adapter_document(conn, authority)
                _checkpoint(Path(args.checkpoint_dir), authority.source_key, authority)
            refresh_source_status(conn, {authority.source_key for authority in fetched})
            conn.commit()
    print(json.dumps(output, indent=2))


if __name__ == "__main__": main()
