"""Bounded, resumable current eCFR bulk adapter.

eCFR is current and authoritative but unofficial.  A version-date is therefore part of
the document identity and the raw official XML URL remains provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import httpx

from .authority_adapter_store import AdapterDocument, refresh_source_status, upsert_adapter_document
from .database import connect

VERSION_URL = "https://www.ecfr.gov/api/versioner/v1/versions/title-{title}.json"
FULL_XML_URL = "https://www.ecfr.gov/api/versioner/v1/full/{issue_date}/title-{title}.xml"
TITLES_URL = "https://www.ecfr.gov/api/versioner/v1/titles"
GOVINFO_BULK_XML_URL = "https://www.govinfo.gov/bulkdata/ECFR/title-{title}/ECFR-title{title}.xml"
DEFAULT_TITLES = tuple(title for title in range(1, 51) if title != 35)
USER_AGENT = os.getenv("LEGAL_SOURCE_USER_AGENT") or "LegalApp-eCFRSync/0.1 (+https://github.com/mattpainter701/legalapp)"
MAX_XML_BYTES = 256 * 1024 * 1024
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ECFRSnapshot:
    title: int
    issue_date: date
    url: str
    title_name: str | None = None
    up_to_date_as_of: date | None = None

    @property
    def partition_key(self) -> str:
        return f"title-{self.title}"


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def latest_snapshot(title: int, payload: dict) -> ECFRSnapshot:
    versions = payload.get("versions") or payload.get("content_versions") or payload.get("dates") or []
    candidates: list[date] = []
    for item in versions:
        raw = item if isinstance(item, str) else item.get("date") or item.get("issue_date")
        if raw:
            candidates.append(_parse_date(raw))
    if not candidates:
        raise RuntimeError(f"eCFR title {title} returned no version dates")
    issue_date = max(candidates)
    return ECFRSnapshot(title, issue_date, FULL_XML_URL.format(title=title, issue_date=issue_date.isoformat()))


def current_snapshots(payload: dict, titles: tuple[int, ...] | list[int] | None = None) -> list[ECFRSnapshot]:
    """Resolve active titles from the single eCFR title-status response.

    The status API supplies the version identity while GovInfo supplies the
    current per-title bulk XML. Reserved titles are never silently ingested.
    """
    records = payload.get("titles") or []
    active: dict[int, dict] = {}
    for record in records:
        try:
            title = int(record["number"])
        except (KeyError, TypeError, ValueError):
            continue
        if not record.get("reserved") and record.get("latest_issue_date"):
            active[title] = record
    requested = tuple(dict.fromkeys(titles or DEFAULT_TITLES))
    missing = [title for title in requested if title not in active]
    if missing:
        raise RuntimeError(f"eCFR returned no active title metadata for: {missing}")
    return [
        ECFRSnapshot(
            title=title,
            issue_date=_parse_date(active[title]["latest_issue_date"]),
            url=GOVINFO_BULK_XML_URL.format(title=title),
            title_name=str(active[title].get("name") or "") or None,
            up_to_date_as_of=(
                _parse_date(active[title]["up_to_date_as_of"])
                if active[title].get("up_to_date_as_of")
                else None
            ),
        )
        for title in requested
    ]


def _text(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def section_documents(snapshot: ECFRSnapshot, xml: bytes, *, retrieved_at: datetime | None = None) -> list[AdapterDocument]:
    if len(xml) > MAX_XML_BYTES:
        raise RuntimeError(f"eCFR XML exceeds {MAX_XML_BYTES} byte bound")
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise RuntimeError("eCFR XML may not contain DTD or entity declarations")
    root = ET.fromstring(xml)
    artifact_sha256 = hashlib.sha256(xml).hexdigest()
    result: list[AdapterDocument] = []
    for section in root.iter():
        local_name = section.tag.rsplit("}", 1)[-1]
        if local_name not in {"SECTION", "SECT"} and section.attrib.get("TYPE") != "SECTION":
            continue
        number = _text(next((x for x in section if x.tag.rsplit("}", 1)[-1] in {"SECTNO", "SECTIONNO"}), None))
        if not number:
            number = str(section.attrib.get("N") or "").strip()
        if not number:
            continue
        heading = _text(next((x for x in section if x.tag.rsplit("}", 1)[-1] in {"SUBJECT", "HD", "HEAD"}), None))
        text = _text(section)
        if len(text) < 20:
            continue
        normalized = number.replace("§", "").strip()
        heading = re.sub(
            rf"^§?\s*{re.escape(normalized)}\s*",
            "",
            heading,
        ).strip()
        citation = f"{snapshot.title} CFR § {normalized}"
        result.append(AdapterDocument(
            source_key="govinfo:ecfr", external_id=f"ecfr:{snapshot.issue_date}:title-{snapshot.title}:{normalized}",
            document_type="regulation_section", title=f"{citation} {heading}".strip(), citation=citation,
            jurisdiction="US", authority_tier="binding_primary", canonical_url=snapshot.url, text=text,
            effective_date=snapshot.issue_date, retrieved_at=retrieved_at,
            raw_media_type="application/xml", parser_version="ecfr-section-xml-v2",
            metadata={"ecfr_title": snapshot.title, "ecfr_title_name": snapshot.title_name,
                      "issue_date": snapshot.issue_date.isoformat(),
                      "up_to_date_as_of": snapshot.up_to_date_as_of.isoformat() if snapshot.up_to_date_as_of else None,
                      "stable_id": f"ecfr:title-{snapshot.title}:{normalized}", "version_id": snapshot.issue_date.isoformat(),
                      "artifact_sha256": artifact_sha256, "official_status": "authoritative_unofficial"},
        ))
    if not result:
        raise RuntimeError("eCFR XML contained no parseable sections")
    return result


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Return a bounded Retry-After delay or exponential fallback."""
    if response is not None:
        retry_after = response.headers.get("retry-after", "").strip()
        try:
            return min(60.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return float(2 ** attempt)


def request_json(client: httpx.Client, url: str, *, retries: int = 3) -> dict:
    attempts = max(1, retries)
    for attempt in range(attempts):
        response: httpx.Response | None = None
        try:
            response = client.get(url)
            if response.status_code in TRANSIENT_STATUS_CODES and attempt + 1 < attempts:
                time.sleep(_retry_delay(response, attempt))
                continue
            response.raise_for_status()
            return response.json()
        except httpx.RequestError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(_retry_delay(response, attempt))
    raise AssertionError("unreachable")


def fetch_xml(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int = MAX_XML_BYTES,
    retries: int = 3,
) -> bytes:
    attempts = max(1, retries)
    for attempt in range(attempts):
        response: httpx.Response | None = None
        try:
            with client.stream("GET", url) as response:
                if response.status_code in TRANSIENT_STATUS_CODES and attempt + 1 < attempts:
                    time.sleep(_retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                announced = response.headers.get("content-length")
                if announced and int(announced) > max_bytes:
                    raise RuntimeError(f"eCFR XML exceeds {max_bytes} byte bound")
                raw = bytearray()
                for block in response.iter_bytes():
                    raw.extend(block)
                    if len(raw) > max_bytes:
                        raise RuntimeError(f"eCFR XML exceeded {max_bytes} byte bound")
                return bytes(raw)
        except httpx.RequestError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(_retry_delay(response, attempt))
    raise AssertionError("unreachable")


def checkpoint_path(base: Path, snapshot: ECFRSnapshot) -> Path:
    return base / f"ecfr-{snapshot.partition_key}.json"


def raw_xml_path(base: Path, snapshot: ECFRSnapshot) -> Path:
    return base / f"ECFR-title{snapshot.title}-{snapshot.issue_date.isoformat()}.xml"


def retain_raw_xml(base: Path, snapshot: ECFRSnapshot, xml: bytes) -> Path:
    """Atomically retain an exact source artifact for audit and retry reuse."""
    base.mkdir(parents=True, exist_ok=True)
    path = raw_xml_path(base, snapshot)
    if path.exists() and path.stat().st_size == len(xml):
        return path
    partial = path.with_suffix(f"{path.suffix}.part")
    partial.write_bytes(xml)
    partial.replace(path)
    return path


def fetch_or_load_xml(
    client: httpx.Client,
    snapshot: ECFRSnapshot,
    *,
    raw_dir: Path | None = None,
) -> tuple[bytes, Path | None]:
    """Reuse a versioned local artifact or fetch and retain the official XML."""
    path = raw_xml_path(raw_dir, snapshot) if raw_dir is not None else None
    if path is not None and path.exists():
        xml = path.read_bytes()
        if len(xml) > MAX_XML_BYTES:
            raise RuntimeError(f"eCFR XML exceeds {MAX_XML_BYTES} byte bound")
        return xml, path
    xml = fetch_xml(client, snapshot.url)
    return xml, retain_raw_xml(raw_dir, snapshot, xml) if raw_dir is not None else None


def sync_title(
    snapshot: ECFRSnapshot,
    xml: bytes,
    *,
    checkpoint_dir: Path,
    limit: int | None,
    dry_run: bool,
    db_url: str | None,
    refresh_status: bool = True,
    prepared_documents: list[AdapterDocument] | None = None,
    result_limit: int | None = None,
) -> list[dict]:
    documents = prepared_documents
    if documents is None:
        documents = section_documents(snapshot, xml, retrieved_at=datetime.now(timezone.utc))
        documents = documents[:limit] if limit is not None else documents
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    state = {"version_id": snapshot.issue_date.isoformat(), "artifact_sha256": hashlib.sha256(xml).hexdigest(), "completed": 0}
    path = checkpoint_path(checkpoint_dir, snapshot)
    if path.exists() and not dry_run:
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous.get("version_id") == state["version_id"] and previous.get("artifact_sha256") == state["artifact_sha256"]:
                state["completed"] = min(int(previous.get("completed", 0)), len(documents))
        except (ValueError, OSError):
            pass
    if dry_run:
        selected = documents[:result_limit] if result_limit is not None else documents
        return [
            {"external_id": doc.external_id, "title": doc.title, "dry_run": True}
            for doc in selected
        ]
    results = []
    with connect(db_url) as conn:
        for doc in documents[state["completed"]:]:
            result = upsert_adapter_document(conn, doc)
            if result_limit is None or len(results) < result_limit:
                results.append(result)
            state["completed"] += 1
            path.write_text(json.dumps(state), encoding="utf-8")
        if state["completed"] == len(documents):
            with conn.cursor() as cursor:
                cursor.execute(
                    """UPDATE legal_documents
                       SET document_status = 'superseded', termination_date = CURRENT_DATE,
                           updated_at = now()
                       WHERE source_key = 'govinfo:ecfr'
                         AND metadata->>'ecfr_title' = %s
                         AND metadata->>'version_id' <> %s
                         AND document_status = 'current'""",
                    [str(snapshot.title), snapshot.issue_date.isoformat()],
                )
        if refresh_status:
            refresh_source_status(conn, {"govinfo:ecfr"})
        conn.commit()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync bounded eCFR sections")
    parser.add_argument("--title", type=int, action="append", dest="titles")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--checkpoint-dir", default=".legalapp-checkpoints")
    parser.add_argument("--db-url")
    args = parser.parse_args()
    with httpx.Client(timeout=90, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml"}) as client:
        output = []
        snapshots = current_snapshots(request_json(client, TITLES_URL), args.titles)
        for snapshot in snapshots:
            xml, _ = fetch_or_load_xml(client, snapshot)
            output.extend(sync_title(snapshot, xml, checkpoint_dir=Path(args.checkpoint_dir), limit=args.limit, dry_run=args.dry_run, db_url=args.db_url))
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
