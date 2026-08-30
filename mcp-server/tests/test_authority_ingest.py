import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mcp_server.authority_ingest as authority_ingest  # noqa: E402
from mcp_server.authority_ingest import (  # noqa: E402
    FetchedDocument,
    ManifestValidationError,
    fetch_document,
    extracted_text_status,
    html_main_text,
    load_manifest,
    preview,
    selected_documents,
    validate_manifest,
)
from mcp_server.source_catalog import load_catalog  # noqa: E402


def test_html_parser_prefers_main_and_removes_navigation_and_scripts():
    text = html_main_text(
        """
        <html><body>
          <header>Site title</header>
          <nav>Navigation noise</nav>
          <main>
            <h1>Medicaid Estate Recovery</h1>
            <p>States must recover certain benefits.</p>
            <script>window.bad = 'not authority';</script>
          </main>
          <footer>Footer noise</footer>
        </body></html>
        """
    )

    assert "Medicaid Estate Recovery" in text
    assert "States must recover" in text
    assert "Navigation noise" not in text
    assert "not authority" not in text
    assert "Footer noise" not in text


def test_pdf_font_map_glyph_output_is_blocked_before_embedding():
    assert extracted_text_status("/0/1/2/i255" * 200) == "blocked_font_map"
    assert extracted_text_status("Federal Rules of Civil Procedure " * 200) == "passed_heuristic"


def test_bundled_authority_manifest_passes_source_policy_validation():
    catalog = load_catalog()
    manifest = load_manifest()

    validate_manifest(manifest, catalog)

    assert len(manifest["documents"]) >= 16
    federal = [
        item for item in manifest["documents"] if item["source_key"] == "uscourts:federal-rules"
    ]
    assert len(federal) == 6
    assert {item["document_type"] for item in federal} == {"court_rules"}
    assert all(item["source_index_url"].startswith("https://www.uscourts.gov/") for item in federal)
    assert all(item["parser_version"] == "pdf-text-v1" for item in federal)
    blocked = next(item for item in federal if item.get("sync_enabled") is False)
    assert blocked["external_id"] == "federal-rules-appellate-2025-12-01"
    assert "font-map" in blocked["sync_disabled_reason"]


def test_manifest_requires_per_artifact_provenance():
    catalog = load_catalog()
    manifest = copy.deepcopy(load_manifest())
    manifest["documents"][0].pop("acquisition_basis")

    with pytest.raises(ManifestValidationError, match="acquisition_basis"):
        validate_manifest(manifest, catalog)


def test_manifest_rejects_invalid_coverage_date():
    catalog = load_catalog()
    manifest = copy.deepcopy(load_manifest())
    manifest["documents"][0]["coverage_start_date"] = "2026/08/15"

    with pytest.raises(ManifestValidationError, match="coverage_start_date must be YYYY-MM-DD"):
        validate_manifest(manifest, catalog)


def test_manifest_requires_a_reason_when_document_sync_is_disabled():
    catalog = load_catalog()
    manifest = copy.deepcopy(load_manifest())
    manifest["documents"][0]["sync_enabled"] = False

    with pytest.raises(ManifestValidationError, match="sync_disabled_reason"):
        validate_manifest(manifest, catalog)


def test_manifest_policy_rejects_robots_blocked_ohio_source():
    catalog = load_catalog()
    manifest = copy.deepcopy(load_manifest())
    manifest["documents"].append(
        {
            "source_key": "ohio:laws",
            "external_id": "orc-2710",
            "document_type": "statute_chapter",
            "title": "Ohio Revised Code Chapter 2710",
            "canonical_url": "https://codes.ohio.gov/ohio-revised-code/chapter-2710",
            "jurisdiction": "OH",
                "authority_tier": "binding_primary",
                "official_status": "official",
                "parser": "html_main_text",
                "parser_version": "html-main-text-v1",
                "acquisition_basis": "Test-only blocked source.",
                "coverage_notes": "Test-only blocked source.",
                "practice_areas": ["mediation"],
        }
    )

    with pytest.raises(ManifestValidationError, match="blocked by the source access policy"):
        validate_manifest(manifest, catalog)


def test_fetch_document_normalizes_text_and_hashes_it():
    html = "<html><body><main><h1>Official guidance</h1><p>" + ("Useful text. " * 30) + "</p></main></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=html,
            headers={
                "content-type": "text/html; charset=utf-8",
                "etag": '"abc"',
                "last-modified": "Wed, 01 Jul 2026 12:00:00 GMT",
            },
        )

    document = load_manifest()["documents"][0]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetched = fetch_document(document, client=client)

    assert fetched.text.startswith("Official guidance")
    assert len(fetched.content_hash) == 64
    assert len(fetched.raw_content_hash) == 64
    assert fetched.raw_content == html.encode()
    assert fetched.resolved_url == str(httpx.URL(document["canonical_url"]))
    assert fetched.etag == '"abc"'
    assert fetched.source_modified_at.year == 2026


def test_fetch_document_rejects_artifact_over_configured_size():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 101,
            headers={"content-type": "text/html", "content-length": "101"},
        )

    document = load_manifest()["documents"][0]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="exceeds 100 bytes"):
            fetch_document(document, client=client, max_document_bytes=100)


def test_manifest_accepts_reviewed_pdf_parser():
    catalog = load_catalog()
    manifest = copy.deepcopy(load_manifest())
    manifest["documents"][0]["parser"] = "pdf_text"
    manifest["documents"][0]["canonical_url"] = "https://www.medicaid.gov/example.pdf"

    validate_manifest(manifest, catalog)


def test_document_selection_is_source_scoped_and_bounded():
    manifest = load_manifest()

    selected = selected_documents(manifest, ["cms:internet-only-manuals"], 1)

    assert len(selected) == 1
    assert selected[0]["source_key"] == "cms:internet-only-manuals"


def test_sync_selection_skips_parser_blocked_documents_without_hiding_preview():
    manifest = load_manifest()

    preview_documents = selected_documents(
        manifest,
        ["uscourts:federal-rules"],
        None,
    )
    sync_documents = selected_documents(
        manifest,
        ["uscourts:federal-rules"],
        None,
        for_sync=True,
    )

    assert len(preview_documents) == 6
    assert len(sync_documents) == 5
    assert all(document.get("sync_enabled", True) for document in sync_documents)


def test_preview_collection_writes_raw_text_and_audit_manifest(tmp_path, monkeypatch):
    document = next(
        item
        for item in load_manifest()["documents"]
        if item["external_id"] == "tax-court-reports-165-5"
    )
    normalized_text = "Published Tax Court opinion text.\n" * 20
    fetched = FetchedDocument(
        text=normalized_text,
        content_hash=hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
        media_type="application/pdf",
        retrieved_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        source_modified_at=None,
        etag='"pamphlet"',
        raw_content=b"%PDF-reviewed-preview",
        raw_content_hash="b" * 64,
        resolved_url=document["canonical_url"],
    )
    monkeypatch.setattr(authority_ingest, "fetch_document", lambda *args, **kwargs: fetched)

    results = preview([document], delay_seconds=0, output_dir=tmp_path)

    assert results[0]["raw_content_hash"] == "b" * 64
    assert results[0]["parser_version"] == "pdf-text-v1"
    assert results[0]["embedding_readiness"] == "pending_chunk_review"
    assert (tmp_path / results[0]["raw_path"]).read_bytes() == fetched.raw_content
    normalized_path = tmp_path / results[0]["normalized_path"]
    assert normalized_path.read_text(encoding="utf-8") == fetched.text
    assert hashlib.sha256(normalized_path.read_bytes()).hexdigest() == fetched.content_hash
    report = json.loads((tmp_path / "preview-manifest.json").read_text(encoding="utf-8"))
    assert report["document_count"] == 1
    assert report["documents"][0]["canonical_url"] == document["canonical_url"]
    assert report["documents"][0]["acquisition_basis"] == document["acquisition_basis"]
    assert "publication_year" in report["documents"][0]
