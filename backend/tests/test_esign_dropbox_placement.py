from types import SimpleNamespace
import uuid
import hashlib
import json
from io import BytesIO
from pypdf import PdfWriter

import pytest

import app.services.esign.dropbox_sign as dropbox


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"signature_request": {"signature_request_id": "env-1"}}


class _Client:
    last = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, *args, **kwargs):
        _Client.last = (args, kwargs)
        return _Response()


@pytest.mark.asyncio
async def test_dropbox_payload_contains_role_bound_positioned_form_field(monkeypatch):
    monkeypatch.setattr(dropbox.httpx, "AsyncClient", lambda **kwargs: _Client())
    monkeypatch.setattr(
        dropbox,
        "get_settings",
        lambda: SimpleNamespace(
            DROPBOX_SIGN_API_KEY="key",
            ESIGN_PROVIDER_BASE_URL="https://sign.test",
            DEV_MODE=True,
        ),
    )
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    source_buffer = BytesIO()
    writer.write(source_buffer)
    source = source_buffer.getvalue()
    source_digest = hashlib.sha256(source).hexdigest()
    request = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        source_document_bytes=source,
        source_document_filename="generated.pdf",
        signers=[
            SimpleNamespace(
                email="client@example.com", name="Client", role="client", sign_order=0
            ),
            SimpleNamespace(
                email="lawyer@example.com", name="Lawyer", role="lawyer", sign_order=1
            ),
        ],
        positioned_fields=[
            {
                "field_id": "client-signature",
                "field_type": "signature",
                "role": "client",
                "page": 1,
                "rect": [72, 100, 216, 136],
                "page_width": 612,
                "page_height": 792,
                "source_sha256": source_digest,
            },
            {
                "field_id": "lawyer-signature",
                "field_type": "signature",
                "role": "lawyer",
                "page": 1,
                "rect": [360, 100, 504, 136],
                "page_width": 612,
                "page_height": 792,
                "source_sha256": source_digest,
            },
        ],
    )
    assert await dropbox.DropboxSignProvider().send(request) == "env-1"
    data = json.loads(_Client.last[1]["data"]["form_fields_per_document"])
    assert data[0]["signer"] == 0
    assert data[0]["page"] == 1
    assert data[0]["y"] == 656
    assert data[0]["required"] is True
    assert data[1]["signer"] == 1
    request.positioned_fields[0]["source_sha256"] = "a" * 64
    with pytest.raises(RuntimeError, match="stale"):
        await dropbox.DropboxSignProvider().send(request)
