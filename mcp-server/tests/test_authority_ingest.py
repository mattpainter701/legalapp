import copy
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.authority_ingest import (
    ManifestValidationError,
    fetch_document,
    html_main_text,
    load_manifest,
    selected_documents,
    validate_manifest,
)
from mcp_server.source_catalog import load_catalog


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


def test_bundled_authority_manifest_passes_source_policy_validation():
    catalog = load_catalog()
    manifest = load_manifest()

    validate_manifest(manifest, catalog)

    assert len(manifest["documents"]) >= 3


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
            "parser": "html_main_text",
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
