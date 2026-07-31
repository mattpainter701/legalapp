import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.cms_adapter import coverage_document, discover_cms_manifest, paged_coverage_items, public_fields
from mcp_server.cms_ingest import _checkpoint_completed, _write_checkpoint


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
