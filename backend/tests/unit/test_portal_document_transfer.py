import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from app.routers import client_portal as r
from app.services import portal_document_transfer as transfer


@pytest.mark.parametrize(
    "url",
    [
        "http://files.example/a",
        "javascript:alert(1)",
        "https://user:pass@files.example/a",
        "https://files.example/\npath",
        "https://",
        "https://files.example/has space",
        "https://files.example/\\path",
    ],
)
def test_link_validation(url):
    with pytest.raises((ValueError, ValidationError)):
        r.PortalUploadLink(url=url)


def test_empty_and_valid_links():
    assert r.PortalUploadLink(url=" ").url is None
    assert (
        transfer.upload_link(" https://files.example/request?token=abc ")
        == "https://files.example/request?token=abc"
    )


@pytest.mark.parametrize(
    "path",
    ["../scan.png", "/Smith/scan.png", "Smith/other.png", "a/b/c/d/e/f/g/h/i/scan.png"],
)
def test_source_paths_are_relative_bounded_and_match_the_file(path):
    with pytest.raises(ValueError):
        transfer.upload_path(path, "scan.png")


def test_source_path():
    assert transfer.upload_path(None, "scan.png") is None
    assert (
        transfer.upload_path("Smith\\scans\\scan.png", "scan.png")
        == "Smith/scans/scan.png"
    )


@pytest.mark.asyncio
async def test_folders_reuse_existing_parents_with_matter_and_tenant_scope(monkeypatch):
    tenant, matter = uuid.uuid4(), uuid.uuid4()
    root, existing, leaf = [SimpleNamespace(id=uuid.uuid4()) for _ in range(3)]
    db = MagicMock(scalar=AsyncMock(side_effect=[existing, None]))
    create = AsyncMock(return_value=leaf)
    monkeypatch.setattr(transfer, "create_folder", create)
    assert (
        await transfer.destination_folder(
            db, tenant_id=tenant, matter_id=matter, root=root, path="Smith/scans/a.png"
        )
        is leaf
    )
    create.assert_awaited_once_with(
        db, tenant_id=tenant, matter_id=matter, parent_id=existing.id, name="scans"
    )
    params = db.scalar.call_args_list[0].args[0].compile().params
    assert (
        tenant in params.values()
        and matter in params.values()
        and root.id in params.values()
    )
    assert (
        await transfer.destination_folder(
            db, tenant_id=tenant, matter_id=matter, root=root, path=None
        )
        is root
    )


@pytest.mark.asyncio
async def test_published_link_uses_only_scoped_latest_event():
    tenant, matter = uuid.uuid4(), uuid.uuid4()
    db = MagicMock(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                metadata_json={"url": "https://files.example/request"}
            )
        )
    )
    assert (
        await transfer.shared_upload_link(db, tenant_id=tenant, matter_id=matter)
        == "https://files.example/request"
    )
    params = db.scalar.call_args.args[0].compile().params
    assert tenant in params.values() and matter in params.values()
    db.scalar.return_value = None
    assert (
        await transfer.shared_upload_link(db, tenant_id=tenant, matter_id=matter)
        is None
    )


@pytest.mark.asyncio
async def test_staff_link_access_and_audited_publication(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="attorney")
    matter = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        cloud_folder={"onedrive": {"url": "private"}},
    )
    db = MagicMock(commit=AsyncMock())
    monkeypatch.setattr(r, "get_current_user", AsyncMock(return_value=user))
    monkeypatch.setattr(r, "set_tenant_context", AsyncMock())
    access = AsyncMock(return_value=False)
    monkeypatch.setattr(r, "can_access_matter", access)
    monkeypatch.setattr(r, "_get_matter_for_firm", AsyncMock(return_value=matter))
    with pytest.raises(HTTPException) as exc:
        await r.firm_get_upload_link(matter.id, None, db)
    assert exc.value.status_code == 404
    access.return_value = True
    body = r.PortalUploadLink(url="https://files.example/request")
    assert await r.firm_set_upload_link(matter.id, body, None, db) == body
    event = db.add.call_args.args[0]
    assert event.created_by == user.id and event.matter_id == matter.id
    assert event.metadata_json == {"url": body.url}
    assert matter.cloud_folder == {"onedrive": {"url": "private"}}
    shared = AsyncMock(return_value=body.url)
    monkeypatch.setattr(r, "shared_upload_link", shared)
    assert (await r.firm_get_upload_link(matter.id, None, db)).url == body.url
    assert (await r.portal_get_upload_link((None, matter), db)).url == body.url
    await r.firm_set_upload_link(matter.id, r.PortalUploadLink(), None, db)
    assert db.add.call_args.args[0].metadata_json == {"url": None}


@pytest.mark.asyncio
async def test_folder_upload_keeps_names_scope_storage_and_replays(monkeypatch):
    tenant, matter_id = uuid.uuid4(), uuid.uuid4()
    ctx = SimpleNamespace(tenant_id=str(tenant), matter_id=str(matter_id))
    matter = SimpleNamespace(slug="smith", cloud_folder={})
    folder = SimpleNamespace(id=uuid.uuid4())
    db = MagicMock(
        scalar=AsyncMock(return_value=None), commit=AsyncMock(), refresh=AsyncMock()
    )

    async def refresh(doc):
        doc.created_at = datetime.now(timezone.utc)

    db.refresh.side_effect = refresh
    monkeypatch.setattr(r, "ensure_system_folder", AsyncMock(return_value=folder))
    destination = AsyncMock(return_value=folder)
    monkeypatch.setattr(r, "destination_folder", destination)
    stored = SimpleNamespace(
        storage_path="stored",
        provider="local",
        backend="local",
        provider_item_id=None,
        drive_id=None,
        parent_id=None,
        error=None,
    )
    storage = AsyncMock(return_value=stored)
    monkeypatch.setattr(r.matter_file_store, "store_matter_file_result", storage)
    monkeypatch.setattr(r, "reject_oversized_request", lambda *args: None)

    async def upload(path="Smith/scans/a.png", filename="a.png", content=b"scan"):
        return await r.portal_upload_document(
            None,
            UploadFile(filename=filename, file=io.BytesIO(content)),
            "Scan",
            (ctx, matter),
            db,
            path,
        )

    response = await upload()
    doc = db.add.call_args.args[0]
    assert (
        doc.matter_id == matter_id
        and doc.tenant_id == tenant
        and doc.folder_id == folder.id
    )
    assert response.filename == "a.png" and doc.portal_visible
    assert storage.call_args.kwargs["folder_path"] == [
        "client_uploads",
        "Smith",
        "scans",
    ]
    assert storage.call_args.kwargs["filename"].endswith("_a.png")
    db.scalar.return_value = doc
    assert (await upload()).id == response.id
    assert storage.await_count == 1
    doc.portal_visible = False
    with pytest.raises(HTTPException) as exc:
        await upload()
    assert exc.value.status_code == 409
    db.scalar.return_value = None
    db.flush = AsyncMock()
    email = b"From: client@example.com\r\nSubject: Old email\r\n\r\nHistorical body"
    email_response = await upload("Smith/mail.eml", "mail.eml", email)
    correspondence = db.add.call_args.args[0]
    assert str(correspondence.document_id) == email_response.id
    assert (
        correspondence.subject == "Old email"
        and correspondence.body == "Historical body"
    )
    assert correspondence.channel == "email" and correspondence.status == "logged"
    assert storage.call_args.kwargs["content"] == email
    stored.error = "unavailable"
    with pytest.raises(HTTPException) as exc:
        await upload()
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc:
        await upload("../a.png")
    assert exc.value.status_code == 400
    destination.side_effect = r.DocumentOrganizationError(400, "depth", "Too deep")
    with pytest.raises(HTTPException) as exc:
        await upload()
    assert exc.value.detail == "Too deep"
