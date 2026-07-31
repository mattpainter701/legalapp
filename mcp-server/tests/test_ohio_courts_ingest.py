import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.ohio_authority_ingest import (
    DEFAULT_TARGETS,
    OhioCourtsConfig,
    discover_documents,
    fetch_with_retries,
    to_fetched_document,
)


def test_discovery_only_accepts_direct_official_allowlisted_documents():
    fixture = (Path(__file__).parent / "fixtures" / "ohio_courts_rules_index.html").read_text(encoding="utf-8")
    config = OhioCourtsConfig(contact="ops@example.com")

    documents = discover_documents(fixture, DEFAULT_TARGETS[0], config)

    assert [doc.canonical_url for doc in documents] == [
        "https://www.supremecourt.ohio.gov/docs/LegalResources/Rules/civil/Civil.pdf",
        "https://www.supremecourt.ohio.gov/docs/LegalResources/Rules/superintendence/Superintendence.pdf",
    ]
    assert documents[0].external_id.startswith("ohio-courts-")


def test_fetch_honors_retry_after_and_maximum_download_size():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"})
        return httpx.Response(200, content=b"<html><body>ok</body></html>", headers={"content-type": "text/html", "etag": '"v2"'})

    pauses = []
    config = OhioCourtsConfig(contact="ops@example.com", min_interval_seconds=0)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_with_retries(client, "https://www.supremecourt.ohio.gov/opinions/", headers={"If-None-Match": '"v1"'}, config=config, sleep=pauses.append)

    assert result.status_code == 200
    assert pauses == [3.0]
    assert calls[0].headers["if-none-match"] == '"v1"'


def test_conditional_not_modified_returns_without_content():
    config = OhioCourtsConfig(contact="ops@example.com")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(304, headers={"etag": '"same"'}))) as client:
        result = fetch_with_retries(client, "https://www.supremecourt.ohio.gov/opinions/", headers={}, config=config, sleep=lambda _: None)
    assert result.status_code == 304
    assert result.content == b""


def test_html_extraction_produces_checksum_ready_document():
    from mcp_server.ohio_authority_ingest import FetchResponse

    response = FetchResponse(200, b"<html><body><main><h1>Ohio Rule</h1><p>" + b"Official court text. " * 5 + b"</p></main></body></html>", "text/html", '"tag"', None)
    fetched = to_fetched_document(response, parser="html")

    assert fetched.text.startswith("Ohio Rule")
    assert len(fetched.content_hash) == 64


def test_resume_document_filter_starts_after_saved_cursor():
    from mcp_server.ohio_authority_ingest import DiscoveredDocument, _resume_documents

    target = DEFAULT_TARGETS[0]
    items = [
        DiscoveredDocument(target, f"https://www.supremecourt.ohio.gov/docs/LegalResources/Rules/{name}.pdf", name)
        for name in ("a", "b", "c")
    ]

    class Cursor:
        def execute(self, *_args):
            pass
        def fetchone(self):
            return (items[1].canonical_url,)
        def __enter__(self): return self
        def __exit__(self, *_args): pass
    class Conn:
        def cursor(self): return Cursor()

    assert [item.title for item in _resume_documents(Conn(), items)] == ["c"]
