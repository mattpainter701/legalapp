import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server import cms_ingest
from mcp_server.authority_adapter_store import AdapterDocument
from mcp_server.cms_adapter import CMSManifestEntry, coverage_document, discover_cms_manifest, paged_coverage_items
from mcp_server.cms_ingest import (
    _checkpoint_completed,
    _fetch_discovered_documents,
    _record_source_failures,
    _write_checkpoint,
)


class _StatusCursor:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.statements.append((sql, params))


class _StatusConnection:
    def __init__(self):
        self.cursor_obj = _StatusCursor()

    def cursor(self):
        return self.cursor_obj


def test_coverage_document_excludes_licensed_code_description_fields():
    document = coverage_document("lcd", {
        "lcdId": "L12345", "title": "Sample LCD", "effectiveDate": "2026-07-01",
        "narrative": "Coverage applies when medically necessary.",
        "hcpcsCode": "A0001", "codeDescription": "Licensed descriptor", "nested": {"CPT": "99999"},
    })

    assert "Licensed descriptor" not in document.text
    assert "A0001" not in document.text
    assert document.document_type == "local_coverage_determination"
    assert document.metadata["licensed_fields_excluded"] is True


def test_coverage_pagination_obeys_limit_with_fixture_transport():
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "page=2" in str(request.url):
            return httpx.Response(200, json={"results": [{"id": "2"}]})
        return httpx.Response(200, json={"results": [{"id": "1"}], "next": "?page=2"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        rows = list(paged_coverage_items(client, "lcd", limit=2))
    assert [row["id"] for row in rows] == ["1", "2"]
    assert len(calls) == 2


def test_manual_manifest_discovery_keeps_only_cms_review_candidates():
    html = '''<a href="/regulations-and-guidance/guidance/manuals/downloads/bp102c01.pdf">Medicare Benefit Policy Manual</a>
    <a href="/files/document/marketplace.pdf">Marketplace help desk</a>
    <a href="https://evil.example/x.pdf">bad</a>
    <a href="/regulations-and-guidance/guidance/manuals/internet-only-manuals-ioms-items/cms019326">100-16</a>'''
    entries = discover_cms_manifest(html, page_url="https://www.cms.gov/medicare/regulations-guidance/manuals/internet-only-manuals-ioms", kind="manual")
    assert len(entries) == 2
    assert all(entry.source_key == "cms:internet-only-manuals" for entry in entries)
    assert all(entry.canonical_url.startswith("https://www.cms.gov/") for entry in entries)


def test_completed_cms_checkpoint_rechecks_stable_ids_but_running_checkpoint_resumes(tmp_path):
    _write_checkpoint(tmp_path, "ncd", 12, status="running")
    assert _checkpoint_completed(tmp_path, "ncd") == 12

    _write_checkpoint(tmp_path, "ncd", 12, status="complete")
    assert _checkpoint_completed(tmp_path, "ncd") == 0


def test_partial_cms_checkpoint_resumes_after_processed_items(tmp_path):
    _write_checkpoint(
        tmp_path,
        "ncd",
        7,
        status="partial_failure",
        failures=[{"source_key": "cms:medicare-coverage-api", "error": "bad item"}],
    )

    assert _checkpoint_completed(tmp_path, "ncd") == 7


def test_bad_cms_artifact_is_recorded_and_later_artifacts_continue(monkeypatch):
    entries = [
        CMSManifestEntry(
            "cms:internet-only-manuals",
            "cms-manual:bad",
            "Unreadable manual",
            "https://www.cms.gov/files/bad.pdf",
            "cms_manual_artifact",
            "https://www.cms.gov/manuals",
            cms_ingest.datetime.now(cms_ingest.timezone.utc),
        ),
        CMSManifestEntry(
            "cms:internet-only-manuals",
            "cms-manual:good",
            "Readable manual",
            "https://www.cms.gov/files/good.pdf",
            "cms_manual_artifact",
            "https://www.cms.gov/manuals",
            cms_ingest.datetime.now(cms_ingest.timezone.utc),
        ),
    ]

    def fake_fetch(entry, *, client):
        if entry.external_id.endswith("bad"):
            raise RuntimeError("CMS artifact produced too little readable text")
        return AdapterDocument(
            source_key=entry.source_key,
            external_id=entry.external_id,
            document_type=entry.document_type,
            title=entry.title,
            citation=None,
            jurisdiction="US",
            authority_tier="agency_guidance",
            canonical_url=entry.canonical_url,
            text="readable official manual text " * 10,
        )

    monkeypatch.setattr(cms_ingest, "fetch_manifest_document", fake_fetch)
    with httpx.Client() as client:
        documents, failures = _fetch_discovered_documents(entries, client=client)

    assert [document.external_id for document in documents] == ["cms-manual:good"]
    assert failures == [
        {
            "source_key": "cms:internet-only-manuals",
            "stage": "artifact_fetch",
            "error": "CMS artifact produced too little readable text",
            "external_id": "cms-manual:bad",
            "canonical_url": "https://www.cms.gov/files/bad.pdf",
        }
    ]


def test_cms_partial_failure_sets_current_error_without_claiming_full_success():
    conn = _StatusConnection()

    _record_source_failures(
        conn,
        [
            {
                "source_key": "cms:transmittals",
                "stage": "artifact_fetch",
                "external_id": "cms-transmittal:broken",
                "error": "too little readable text",
            }
        ],
    )

    sql, params = conn.cursor_obj.statements[0]
    assert "current_error=%s" in sql
    assert "last_successful_sync_at" not in sql
    assert "cms-transmittal:broken" in params[0]
    assert params[1:] == ["cms:transmittals"] * 4
