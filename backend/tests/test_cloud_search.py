"""Cloud search provider and content hydration regressions."""

from io import BytesIO

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
