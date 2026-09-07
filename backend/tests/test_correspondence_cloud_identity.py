"""Captured mail must retain the provider identity used by subsequent reads."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import correspondence_capture as capture
from app.services import google_mail, microsoft_mail, matter_file_store
from app.services.matter_file_store import StorageResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,backend",
    [
        ("microsoft", "onedrive"),
        ("microsoft", "sharepoint"),
        ("google", "google_drive"),
    ],
)
async def test_captured_eml_can_be_read_by_durable_identity(
    monkeypatch, provider, backend
):
    tenant_id, user_id, matter_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    binding = {"provider": backend}
    matter = SimpleNamespace(
        id=matter_id, slug="synthetic-matter", cloud_folder=binding
    )
    raw = b"Subject: Synthetic\r\n\r\nOnly test content."
    stored = StorageResult(
        provider=provider,
        backend=backend,
        storage_path="https://display.example.invalid/mail.eml",
        provider_item_id="mail-item",
        drive_id="drive-a",
        parent_id="correspondence-folder",
    )
    rows, events = [], []

    class DB:
        async def execute(self, _):
            assert events[-1] == "scope"
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        def add(self, row):
            rows.append(row)

        async def flush(self):
            assert events[-1] == "scope"
            rows[0].id = uuid.uuid4()

        async def commit(self):
            events.append("commit")

    db = DB()

    async def scope(*_):
        events.append("scope")

    async def read_raw(*_):
        matter.__dict__.clear()  # token refresh can expire the ORM object
        events.append("provider_refresh")
        return raw

    writer = AsyncMock(return_value=stored)
    monkeypatch.setattr(capture, "set_tenant_context", scope)
    monkeypatch.setattr(capture, "_already_captured", AsyncMock(return_value=False))
    monkeypatch.setattr(microsoft_mail, "ms_read_mail_raw", read_raw)
    monkeypatch.setattr(google_mail, "gmail_read_raw", read_raw)
    monkeypatch.setattr(capture.matter_file_store, "store_matter_file_result", writer)
    assert await capture.capture_email_for_matter(
        db,
        tenant_id,
        user_id,
        matter,
        {
            "id": "message-a",
            "subject": "Synthetic",
            "from": "self@example.com",
            "to": "self@example.com",
        },
        provider,
        mailbox_address="self@example.com",
    )
    doc, log = rows
    assert doc.tenant_id == tenant_id and doc.matter_id == matter_id
    assert doc.storage_provider == provider and doc.storage_backend == backend
    assert doc.provider_object_id == "mail-item"
    assert doc.provider_drive_id == "drive-a"
    assert doc.provider_parent_id == "correspondence-folder"
    assert log.document_id == doc.id and log.direction == "outbound"
    assert writer.call_args.kwargs["category"] == "correspondence"
    assert writer.call_args.kwargs["matter_cloud_folder"] == binding
    assert events == ["scope", "provider_refresh", "scope", "scope", "commit"]

    # Exercise the actual read contract, only stubbing the provider transport.
    monkeypatch.setattr(
        matter_file_store,
        "get_fresh_token",
        AsyncMock(return_value="synthetic-test-token"),
    )
    downloader = AsyncMock(return_value=raw)
    monkeypatch.setattr(
        capture.matter_file_store, "_download_provider_bytes", downloader
    )
    assert (
        await capture.matter_file_store.read_matter_file_bytes(
            db=db, tenant_id=str(tenant_id), document=doc
        )
        == raw
    )
    url = downloader.call_args.kwargs["url"]
    assert "display.example.invalid" not in url
    assert (
        "/files/mail-item?"
        if provider == "google"
        else "/drives/drive-a/items/mail-item/content"
    ) in url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,token_helper,reader,transport,args",
    [
        (
            microsoft_mail,
            "_ms_get_user_token",
            "ms_read_mail_user",
            "graph_request",
            (),
        ),
        (
            microsoft_mail,
            "_ms_get_user_token",
            "ms_read_mail_raw",
            "graph_request",
            ("message-a",),
        ),
        (google_mail, "_google_get_user_token", "gmail_read_mail", "gmail_request", ()),
        (
            google_mail,
            "_google_get_user_token",
            "gmail_read_raw",
            "gmail_request",
            ("message-a",),
        ),
    ],
)
async def test_disconnected_mail_stops_before_provider_request(
    monkeypatch, module, token_helper, reader, transport, args
):
    user_id = str(uuid.uuid4())
    monkeypatch.setattr(module, token_helper, AsyncMock(return_value=None))
    request = AsyncMock()
    monkeypatch.setattr(module, transport, request)
    with pytest.raises(RuntimeError, match="Connect or reconnect.*Integrations") as exc:
        await getattr(module, reader)(None, str(uuid.uuid4()), user_id, *args)
    assert user_id not in str(exc.value)
    request.assert_not_awaited()
