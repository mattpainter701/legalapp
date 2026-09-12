"""Cloud search provider and content hydration regressions."""

import asyncio
from io import BytesIO
from email.message import EmailMessage

from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import pytest
import httpx

from app.services.cloud_search import CloudHit, CloudSearchService


class _Response:
    status_code = 200
    text = ""

    def json(self):
        return {"files": []}


class _BinaryResponse:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content
        self.headers = {"content-length": str(len(content))}

    async def aiter_bytes(self):
        yield self.content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _BinaryClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        return self.response

    def stream(self, *_args, **_kwargs):
        return self.response


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_bytes(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("account_tier", ["personal", "workspace"])
async def test_drive_search_includes_shared_drive_options(monkeypatch, account_tier):
    """The same request is safe for personal accounts and Workspace tenants."""
    service = CloudSearchService()
    captured: dict = {}

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, headers, params):
            captured.update(params)
            return _Response()

    monkeypatch.setattr(service, "_get_google_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient", lambda **_kwargs: _Client()
    )

    await service._search_google_drive(
        db=None,
        keywords=["matter"],
        date_after="",
        max_hits=10,
        tenant_id=f"tenant-{account_tier}",
        user_id=None,
    )

    assert captured["supportsAllDrives"] is True
    assert captured["includeItemsFromAllDrives"] is True
    assert captured["corpora"] == "allDrives"


@pytest.mark.asyncio
async def test_onedrive_docx_content_is_extracted_before_truncation(monkeypatch):
    service = CloudSearchService()
    document_bytes = _docx_bytes("The synthetic OneDrive fact is DOCX readable.")

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    monkeypatch.setattr(service, "_get_microsoft_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient",
        lambda **_kwargs: _BinaryClient(_BinaryResponse(document_bytes)),
    )

    hit = CloudHit(
        provider="microsoft",
        source="onedrive",
        object_id="docx-id",
        title="synthetic.docx",
        snippet="",
        url="https://example.test/synthetic.docx",
        modified_time="",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    content = await service._fetch_onedrive_content(None, hit, "tenant", 2000, None)

    assert content == "The synthetic OneDrive fact is DOCX readable."
    assert "PK" not in content


@pytest.mark.asyncio
async def test_onedrive_pdf_content_is_extracted_before_truncation(monkeypatch):
    service = CloudSearchService()

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    monkeypatch.setattr(service, "_get_microsoft_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient",
        lambda **_kwargs: _BinaryClient(
            _BinaryResponse(_pdf_bytes("Synthetic PDF fact"))
        ),
    )
    hit = CloudHit(
        provider="microsoft",
        source="onedrive",
        object_id="pdf-id",
        title="synthetic.pdf",
        snippet="",
        url="https://example.test/synthetic.pdf",
        modified_time="",
        mime_type="application/pdf",
    )

    content = await service._fetch_onedrive_content(None, hit, "tenant", 2000, None)

    assert content == "Synthetic PDF fact"
    assert "FlateDecode" not in content


@pytest.mark.asyncio
async def test_google_native_doc_export_is_treated_as_text(monkeypatch):
    service = CloudSearchService()

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    monkeypatch.setattr(service, "_get_google_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient",
        lambda **_kwargs: _BinaryClient(_BinaryResponse(b"Exported Google Doc fact")),
    )
    hit = CloudHit(
        provider="google",
        source="drive",
        object_id="native-doc-id",
        title="native document",
        snippet="",
        url="https://drive.google.test/native-doc-id",
        modified_time="",
        mime_type="application/vnd.google-apps.document",
    )

    content = await service._fetch_google_drive_content(None, hit, "tenant", 2000, None)

    assert content == "Exported Google Doc fact"


@pytest.mark.asyncio
async def test_google_native_sheet_export_is_treated_as_text(monkeypatch):
    service = CloudSearchService()

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    monkeypatch.setattr(service, "_get_google_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient",
        lambda **_kwargs: _BinaryClient(_BinaryResponse(b"row one,synthetic fact")),
    )
    hit = CloudHit(
        provider="google",
        source="drive",
        object_id="native-sheet-id",
        title="native spreadsheet",
        snippet="",
        url="https://drive.google.test/native-sheet-id",
        modified_time="",
        mime_type="application/vnd.google-apps.spreadsheet",
    )

    content = await service._fetch_google_drive_content(None, hit, "tenant", 2000, None)

    assert content == "row one,synthetic fact"


@pytest.mark.asyncio
async def test_download_follows_graph_content_redirects():
    document_bytes = _docx_bytes("Redirected OneDrive fact")

    def handler(request):
        if request.url.path.endswith("/content"):
            return httpx.Response(
                302,
                headers={"location": "https://files.example.test/final"},
            )
        return httpx.Response(
            200,
            content=document_bytes,
            headers={"content-length": str(len(document_bytes))},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        content = await CloudSearchService._download_content(
            client, "https://graph.example.test/content"
        )

    assert content == document_bytes


def test_rfc822_extraction_decodes_multipart_text_and_skips_attachments():
    message = EmailMessage()
    message["Subject"] = "Synthetic retained email"
    message["From"] = "sender@example.test"
    message["To"] = "recipient@example.test"
    message.set_content("The encoded email fact is in the plain text body.")
    message.add_attachment(
        b"secret attachment bytes",
        maintype="application",
        subtype="octet-stream",
        filename="secret.bin",
    )

    hit = CloudHit(
        provider="microsoft",
        source="onedrive",
        object_id="eml-id",
        title="retained.eml",
        snippet="",
        url="",
        modified_time="",
        mime_type="message/rfc822",
    )
    content = CloudSearchService._extract_downloaded_file(bytes(message), hit, 2000)

    assert "Subject: Synthetic retained email" in content
    assert "encoded email fact" in content
    assert "secret attachment bytes" not in content


def test_rfc822_html_only_body_is_plain_text_and_attached_email_is_excluded():
    nested = EmailMessage()
    nested["Subject"] = "Attached message must stay hidden"
    nested.set_content("secret nested message")
    message = EmailMessage()
    message["Subject"] = "HTML retained email"
    message.set_content(
        "<html><head><style>secret style text</style><script>secret script text</script></head>"
        "<body><p>The <b>HTML-only</b> email fact.</p></body></html>",
        subtype="html",
    )
    message.add_attachment(
        bytes(nested), maintype="message", subtype="rfc822", filename="nested.eml"
    )
    hit = CloudHit(
        provider="microsoft",
        source="onedrive",
        object_id="eml-id",
        title="retained.eml",
        snippet="",
        url="",
        modified_time="",
        mime_type="message/rfc822",
    )

    content = CloudSearchService._extract_downloaded_file(bytes(message), hit, 2000)

    assert "HTML-only email fact." in content
    assert "<b>" not in content
    assert "secret style text" not in content
    assert "secret script text" not in content
    assert "secret nested message" not in content


@pytest.mark.asyncio
async def test_outlook_mime_content_uses_rfc822_extractor(monkeypatch):
    message = EmailMessage()
    message["Subject"] = "Outlook MIME fact"
    message.set_content("The Outlook MIME body is readable.")
    service = CloudSearchService()

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    monkeypatch.setattr(service, "_get_microsoft_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient",
        lambda **_kwargs: _BinaryClient(_BinaryResponse(bytes(message))),
    )
    hit = CloudHit(
        provider="microsoft",
        source="outlook",
        object_id="message-id",
        title="Outlook MIME fact",
        snippet="",
        url="",
        modified_time="",
        mime_type="",
    )

    content = await service._fetch_outlook_content(None, hit, "tenant", 2000, None)

    assert "Subject: Outlook MIME fact" in content
    assert "Outlook MIME body is readable." in content


@pytest.mark.asyncio
async def test_cloud_download_rejects_unsupported_binary_without_raw_bytes(monkeypatch):
    service = CloudSearchService()

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    monkeypatch.setattr(service, "_get_microsoft_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient",
        lambda **_kwargs: _BinaryClient(_BinaryResponse(b"\x00\xffbinary")),
    )
    hit = CloudHit(
        provider="microsoft",
        source="onedrive",
        object_id="zip-id",
        title="synthetic.zip",
        snippet="Indexed metadata snippet",
        url="https://example.test/synthetic.zip",
        modified_time="",
        mime_type="application/octet-stream",
    )

    content = await service._fetch_onedrive_content(None, hit, "tenant", 2000, None)

    assert content == "Indexed metadata snippet"
    assert "binary" not in content


@pytest.mark.asyncio
async def test_cloud_download_uses_snippet_when_pdf_has_no_extractable_text():
    hit = CloudHit(
        provider="microsoft",
        source="onedrive",
        object_id="scan-id",
        title="scan.pdf",
        snippet="Scanned PDF metadata",
        url="https://example.test/scan.pdf",
        modified_time="",
        mime_type="application/pdf",
    )

    assert (
        CloudSearchService._extract_downloaded_file(b"not-a-pdf", hit, 2000)
        == hit.snippet
    )


def test_cloud_download_keeps_plain_text_and_respects_character_limit():
    hit = CloudHit(
        provider="microsoft",
        source="onedrive",
        object_id="text-id",
        title="notes.txt",
        snippet="",
        url="https://example.test/notes.txt",
        modified_time="",
        mime_type="text/plain",
    )

    assert CloudSearchService._extract_downloaded_file(b"alpha beta", hit, 5) == "alpha"


@pytest.mark.asyncio
async def test_cloud_download_rejects_announced_oversize_response():
    class _OversizeResponse(_BinaryResponse):
        def __init__(self):
            super().__init__(b"small")
            self.headers["content-length"] = str(10 * 1024 * 1024 + 1)

    client = _BinaryClient(_OversizeResponse())

    assert (
        await CloudSearchService._download_content(client, "https://example.test/file")
        is None
    )


# ── Fan-out latency ──────────────────────────────────────────────────────────
# Provider searches used to be awaited one after another, and every Drive hit
# paid for its own description round trip on a fresh connection, so a matter
# page could sit for fifteen seconds behind the slowest mailbox.


def _hit(object_id: str) -> CloudHit:
    return CloudHit(
        provider="google",
        source="drive",
        object_id=object_id,
        title=object_id,
        snippet="",
        url="",
        modified_time="",
        mime_type="",
    )


@pytest.mark.asyncio
async def test_provider_searches_run_concurrently(monkeypatch):
    """Each source starts before the previous one finishes.

    The two fakes deadlock unless they overlap, so a regression to sequential
    awaits fails this rather than merely slowing it down.
    """
    service = CloudSearchService()
    drive_started = asyncio.Event()
    graph_finished = asyncio.Event()

    async def fake_drive(*_args, **_kwargs):
        drive_started.set()
        await asyncio.wait_for(graph_finished.wait(), timeout=5)
        return [_hit("drive")]

    async def fake_graph(*_args, **_kwargs):
        await asyncio.wait_for(drive_started.wait(), timeout=5)
        graph_finished.set()
        return [_hit("graph")]

    async def fake_index(*_args, **_kwargs):
        return []

    monkeypatch.setattr(service, "_search_google_drive", fake_drive)
    monkeypatch.setattr(service, "_search_graph", fake_graph)
    monkeypatch.setattr(service, "search_index", fake_index)

    hits = await service.search(
        db=None,
        plan={"sources": ["drive", "onedrive"], "keywords": ["acme"]},
        tenant_id="tenant-1",
        user_id="user-1",
        budget_seconds=10,
    )

    assert sorted(hit.object_id for hit in hits) == ["drive", "graph"]


@pytest.mark.asyncio
async def test_source_over_budget_is_dropped_not_awaited(monkeypatch):
    """A stalled provider costs its budget, not the whole response."""
    service = CloudSearchService()

    async def stalled_drive(*_args, **_kwargs):
        await asyncio.sleep(30)
        return [_hit("drive")]

    async def fake_graph(*_args, **_kwargs):
        return [_hit("graph")]

    async def fake_index(*_args, **_kwargs):
        return []

    monkeypatch.setattr(service, "_search_google_drive", stalled_drive)
    monkeypatch.setattr(service, "_search_graph", fake_graph)
    monkeypatch.setattr(service, "search_index", fake_index)

    hits = await service.search(
        db=None,
        plan={"sources": ["drive", "onedrive"], "keywords": ["acme"]},
        tenant_id="tenant-1",
        user_id="user-1",
        budget_seconds=0.05,
    )

    assert [hit.object_id for hit in hits] == ["graph"]


@pytest.mark.asyncio
async def test_failing_source_does_not_lose_the_others(monkeypatch):
    service = CloudSearchService()

    async def broken_drive(*_args, **_kwargs):
        raise RuntimeError("drive is down")

    async def fake_graph(*_args, **_kwargs):
        return [_hit("graph")]

    async def fake_index(*_args, **_kwargs):
        return []

    monkeypatch.setattr(service, "_search_google_drive", broken_drive)
    monkeypatch.setattr(service, "_search_graph", fake_graph)
    monkeypatch.setattr(service, "search_index", fake_index)

    hits = await service.search(
        db=None,
        plan={"sources": ["drive", "onedrive"], "keywords": ["acme"]},
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert [hit.object_id for hit in hits] == ["graph"]


@pytest.mark.asyncio
async def test_drive_snippets_overlap_on_the_search_client(monkeypatch):
    """Descriptions are fetched together, on the connection already open."""
    service = CloudSearchService()
    file_ids = ["f0", "f1", "f2", "f3"]
    created: list[object] = []
    inflight = {"now": 0, "peak": 0}

    class _Payload:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __init__(self):
            created.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers=None, params=None):
            if url.endswith("/files"):
                return _Payload(
                    {"files": [{"id": fid, "name": fid} for fid in file_ids]}
                )
            inflight["now"] += 1
            inflight["peak"] = max(inflight["peak"], inflight["now"])
            await asyncio.sleep(0.01)
            inflight["now"] -= 1
            return _Payload({"description": f"desc-{url.rsplit('/', 1)[-1]}"})

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    monkeypatch.setattr(service, "_get_google_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient", lambda **_kwargs: _Client()
    )

    hits = await service._search_google_drive(
        db=None,
        keywords=["acme"],
        date_after="",
        max_hits=10,
        tenant_id="tenant-1",
        user_id=None,
    )

    assert len(created) == 1, "snippets must reuse the search client"
    assert inflight["peak"] > 1, "snippets must be fetched concurrently"
    assert [hit.snippet for hit in hits] == [f"desc-{fid}" for fid in file_ids]


@pytest.mark.asyncio
async def test_prefetched_token_is_used_instead_of_a_second_lookup(monkeypatch):
    """The fan-out must not reach back into the shared session for a token."""
    service = CloudSearchService()
    lookups = []

    async def fake_token(*_args, **_kwargs):
        lookups.append("google")
        return "access-token"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(service, "_get_google_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient", lambda **_kwargs: _Client()
    )

    await service._search_google_drive(
        db=None,
        keywords=["acme"],
        date_after="",
        max_hits=10,
        tenant_id="tenant-1",
        user_id=None,
        token="prefetched-token",
    )

    assert lookups == []


@pytest.mark.asyncio
async def test_fan_out_is_handed_no_session(monkeypatch):
    """Overlapping sources must not share one AsyncSession.

    Tokens are resolved before the fan-out and the index task keeps the
    session, so a provider coroutine is handed None and can never issue a
    query alongside another one.
    """
    service = CloudSearchService()
    session = object()
    provider_sessions = []
    index_sessions = []
    token_sessions = []

    async def fake_token(db, *_args, **_kwargs):
        token_sessions.append(db)
        return "access-token"

    async def record_provider(db, *_args, **_kwargs):
        provider_sessions.append(db)
        return []

    async def record_index(db, *_args, **_kwargs):
        index_sessions.append(db)
        return []

    monkeypatch.setattr(service, "_get_google_token", fake_token)
    monkeypatch.setattr(service, "_get_microsoft_token", fake_token)
    monkeypatch.setattr(service, "_search_google_drive", record_provider)
    monkeypatch.setattr(service, "_search_graph", record_provider)
    monkeypatch.setattr(service, "search_index", record_index)

    await service.search(
        db=session,
        plan={"sources": ["drive", "onedrive"], "keywords": ["acme"]},
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert provider_sessions == [None, None]
    assert index_sessions == [session]
    # Both tokens are resolved once each, up front, while nothing overlaps.
    assert token_sessions == [session, session]


@pytest.mark.asyncio
async def test_provider_with_no_credential_is_never_scheduled(monkeypatch):
    """An unconnected provider is skipped, not run against a shared session."""
    service = CloudSearchService()
    called = []

    async def no_google_token(*_args, **_kwargs):
        return None

    async def microsoft_token(*_args, **_kwargs):
        return "access-token"

    async def record(name):
        async def provider(*_args, **_kwargs):
            called.append(name)
            return []

        return provider

    monkeypatch.setattr(service, "_get_google_token", no_google_token)
    monkeypatch.setattr(service, "_get_microsoft_token", microsoft_token)
    monkeypatch.setattr(service, "_search_google_drive", await record("drive"))
    monkeypatch.setattr(service, "_search_gmail", await record("gmail"))
    monkeypatch.setattr(service, "_search_graph", await record("graph"))
    monkeypatch.setattr(service, "search_index", await record("index"))

    await service.search(
        db=object(),
        plan={"sources": ["drive", "gmail", "onedrive"], "keywords": ["acme"]},
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert called == ["graph", "index"]


@pytest.mark.asyncio
async def test_gmail_metadata_is_fetched_concurrently_and_stays_aligned(monkeypatch):
    """The message list and its metadata round trips share one client."""
    service = CloudSearchService()
    msg_ids = ["m0", "m1", "m2"]
    created: list[object] = []
    inflight = {"now": 0, "peak": 0}

    class _Payload:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __init__(self):
            created.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers=None, params=None):
            if url.endswith("/messages"):
                return _Payload({"messages": [{"id": mid} for mid in msg_ids]})
            inflight["now"] += 1
            inflight["peak"] = max(inflight["peak"], inflight["now"])
            await asyncio.sleep(0.01)
            inflight["now"] -= 1
            mid = url.rsplit("/", 1)[-1]
            return _Payload(
                {
                    "snippet": f"snippet {mid}",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": f"Subject {mid}"},
                            {"name": "From", "value": f"{mid}@firm.test"},
                        ]
                    },
                }
            )

    async def fake_token(*_args, **_kwargs):
        return "access-token"

    monkeypatch.setattr(service, "_get_google_token", fake_token)
    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient", lambda **_kwargs: _Client()
    )

    hits = await service._search_gmail(
        db=None,
        keywords=["acme"],
        date_after="",
        max_hits=10,
        tenant_id="tenant-1",
        user_id=None,
    )

    assert len(created) == 1, "metadata must reuse the search client"
    assert inflight["peak"] > 1, "metadata must be fetched concurrently"
    # Each hit keeps the subject of its own message: the fan-out returns in
    # completion order, so the zip below it has to pair by position.
    assert [(hit.object_id, hit.title) for hit in hits] == [
        (mid, f"Subject {mid}") for mid in msg_ids
    ]
    assert [hit.snippet for hit in hits] == [f"snippet {mid}" for mid in msg_ids]
    # Reverse-chronological scoring survives the change.
    assert hits[0].relevance_score > hits[-1].relevance_score


@pytest.mark.asyncio
async def test_gmail_metadata_opens_its_own_client_when_called_alone(monkeypatch):
    """Callers outside a search still get a working request."""
    service = CloudSearchService()
    created: list[object] = []

    class _Client:
        def __init__(self):
            created.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, headers=None, params=None):
            class _Payload:
                status_code = 200

                @staticmethod
                def json():
                    return {
                        "snippet": "lone snippet",
                        "payload": {"headers": [{"name": "Subject", "value": "Lone"}]},
                    }

            return _Payload()

    monkeypatch.setattr(
        "app.services.cloud_search.httpx.AsyncClient", lambda **_kwargs: _Client()
    )

    detail = await service._get_gmail_metadata("access-token", "m0")

    assert len(created) == 1
    assert detail["headers"]["Subject"] == "Lone"
    assert detail["snippet"] == "lone snippet"


@pytest.mark.asyncio
async def test_token_lookup_failure_skips_that_provider(monkeypatch):
    """A vault error reads as "not connected", it does not fail the search."""
    service = CloudSearchService()
    called: list[str] = []

    async def broken_google_token(*_args, **_kwargs):
        raise RuntimeError("token vault unavailable")

    async def microsoft_token(*_args, **_kwargs):
        return "access-token"

    def record(name):
        async def provider(*_args, **_kwargs):
            called.append(name)
            return []

        return provider

    monkeypatch.setattr(service, "_get_google_token", broken_google_token)
    monkeypatch.setattr(service, "_get_microsoft_token", microsoft_token)
    monkeypatch.setattr(service, "_search_google_drive", record("drive"))
    monkeypatch.setattr(service, "_search_gmail", record("gmail"))
    monkeypatch.setattr(service, "_search_graph", record("graph"))
    monkeypatch.setattr(service, "search_index", record("index"))

    hits = await service.search(
        db=object(),
        plan={"sources": ["drive", "gmail", "onedrive"], "keywords": ["acme"]},
        tenant_id="tenant-1",
        user_id="user-1",
    )

    assert called == ["graph", "index"]
    assert hits == []


@pytest.mark.asyncio
async def test_cancelling_the_search_cancels_every_source(monkeypatch):
    """A client that walks away must not leave provider calls in flight."""
    service = CloudSearchService()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def stalled_drive(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return []

    async def stalled_index(*_args, **_kwargs):
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(service, "_search_google_drive", stalled_drive)
    monkeypatch.setattr(service, "search_index", stalled_index)

    task = asyncio.ensure_future(
        service.search(
            db=None,
            plan={"sources": ["drive"], "keywords": ["acme"]},
            tenant_id="tenant-1",
            user_id="user-1",
            budget_seconds=30,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(cancelled.wait(), timeout=5)


@pytest.mark.asyncio
async def test_collecting_no_sources_is_not_a_wait():
    assert await CloudSearchService._collect_within_budget([], None) == []


@pytest.mark.asyncio
async def test_graph_and_sharepoint_resolve_their_own_token_when_called_directly():
    """Direct callers keep the per-source lookup the fan-out now skips."""
    service = CloudSearchService()
    lookups: list[str] = []

    async def fake_token(_db, tenant_id, _user_id):
        lookups.append(tenant_id)
        return None

    service._get_microsoft_token = fake_token

    assert (
        await service._search_graph(
            db=object(),
            keywords=["acme"],
            date_after="",
            max_hits=10,
            tenant_id="tenant-1",
            user_id="user-1",
        )
        == []
    )
    assert (
        await service._search_sharepoint_folder(
            db=object(),
            keywords=["acme"],
            max_hits=10,
            tenant_id="tenant-2",
            user_id="user-1",
            drive_id="drive-1",
            folder_id="folder-1",
        )
        == []
    )

    assert lookups == ["tenant-1", "tenant-2"]
