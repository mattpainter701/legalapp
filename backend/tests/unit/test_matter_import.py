"""Import parser, replay, routing and authorization coverage without live providers."""

import io
import uuid
from contextlib import asynccontextmanager
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from app.routers import matter_imports as r
from app.services.matter_import_manifest import (
    file_manifest,
    open_archive,
    parse_eml,
    safe_path,
)

EML = b"From: lawyer@old-firm.example\r\nTo: client@example.com\r\nSubject: [TASK] Historical email\r\nDate: Mon, 01 Jan 2024 12:00:00 -0600\r\nMessage-ID: <old-1>\r\nContent-Type: text/plain\r\n\r\nOld case information"


def zipped(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for path, content in files:
            z.writestr(path, content)
    return buf.getvalue()


@pytest.mark.parametrize(
    "path",
    [
        "../x",
        "/root/x",
        "C:\\x",
        "a//b",
        "a/../b",
        "a/./b",
        "a./b",
        "a\x00b",
        "a:b",
        "x/" * 11 + "a",
    ],
)
def test_unsafe_paths(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_archive_preserves_bytes_and_rejects_collisions():
    with open_archive(zipped([("Smith/mail.eml", EML)])) as z:
        assert z.read("Smith/mail.eml") == EML
    with pytest.raises(ValueError):
        open_archive(zipped([("a", b"a"), ("A", b"b")]))
    with pytest.raises(ValueError):
        open_archive(zipped([("../escape", b"a")]))


def test_archive_limits_and_symlinks(monkeypatch):
    from app.services import matter_import_manifest as parser

    for name, value in [
        ("MAX_FILES", 0),
        ("MAX_EXPANDED_BYTES", 0),
        ("MAX_FILE_BYTES", 0),
        ("MAX_ARCHIVE_BYTES", 0),
    ]:
        with monkeypatch.context() as m:
            m.setattr(parser, name, value)
            with pytest.raises(ValueError):
                open_archive(zipped([("a", b"abc")]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "/outside")
    with pytest.raises(ValueError):
        open_archive(buf.getvalue())


def test_eml_domain_independent_and_no_task_execution():
    parsed = parse_eml(EML, ["lawyer@old-firm.example"])
    assert parsed["direction"] == "outbound"
    assert parsed["subject"] == "[TASK] Historical email"
    assert parsed["occurred_at"] == datetime(2024, 1, 1, 18, tzinfo=timezone.utc)
    assert parse_eml(EML, ["client@example.com"])["direction"] == "inbound"
    assert parse_eml(EML, [])["direction"] == "unknown"
    assert parse_eml(b"Subject: x\nDate: bad\n\nbody", [])["occurred_at"] is None
    with pytest.raises(ValueError):
        parse_eml(b"not an email", [])


def test_html_and_attachment_preserved_without_execution():
    from email.message import EmailMessage

    mail = EmailMessage()
    mail["Subject"] = "Files"
    mail.set_content("<script>alert(1)</script>", subtype="html")
    mail.add_attachment(
        b"attachment", maintype="application", subtype="pdf", filename="case.pdf"
    )
    parsed = parse_eml(mail.as_bytes(), [])
    assert "<script>" not in parsed["body"]
    assert parsed["attachments"] == ["case.pdf"]


def test_schema_requires_mapping_and_unique_manifest():
    with pytest.raises(ValidationError):
        r.Mapping(group="x", matter_name="x")
    with pytest.raises(ValidationError):
        r.Mapping(group="x", first_name="a", last_name="b")
    entry = {**file_manifest("x", b"a"), "group": "x"}
    with pytest.raises(ValidationError):
        r.ImportPlan(id=uuid.uuid4(), files=[entry, entry])
    assert r.Mapping(group="x", exclude=True)
    assert r.Mapping(group="x", matter_name="x", organization_name="Firm")


@pytest.fixture
def ctx(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="user")
    db = SimpleNamespace(
        scalar=AsyncMock(),
        execute=AsyncMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        add=MagicMock(),
    )
    monkeypatch.setattr(r, "set_tenant_context", AsyncMock())
    return db, user


@pytest.mark.asyncio
async def test_plan_and_replay(ctx):
    db, user = ctx
    body = r.ImportPlan(
        id=uuid.uuid4(), files=[{**file_manifest("a", b"a"), "group": "g"}]
    )
    db.scalar.return_value = None
    result = await r.plan(body, db, user)
    assert result["status"] == "review"
    run = db.add.call_args_list[-1].args[0]
    db.scalar.return_value = run
    assert await r.plan(body, db, user) == result
    run.manifest = {"files": []}
    with pytest.raises(HTTPException) as exc:
        await r.plan(body, db, user)
    assert exc.value.status_code == 409
    run.created_by_user_id = uuid.uuid4()
    with pytest.raises(HTTPException):
        await r.plan(body, db, user)


@pytest.mark.asyncio
async def test_access_fails_closed(ctx, monkeypatch):
    db, user = ctx
    db.scalar.return_value = None
    with pytest.raises(HTTPException):
        await r.get_run(db, user, uuid.uuid4())
    monkeypatch.setattr(r, "can_access_matter", AsyncMock(return_value=False))
    with pytest.raises(HTTPException):
        await r.matter_for_user(db, user, uuid.uuid4())


def run_for(user, path="Smith/mail.eml", content=EML):
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="review",
        manifest={"files": [{**file_manifest(path, content), "group": "Smith"}]},
        tenant_id=user.tenant_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["new", "organization", "contact", "existing", "exclude"]
)
async def test_approve_choices_atomic_replay(ctx, monkeypatch, mode):
    db, user = ctx
    run = run_for(user)
    monkeypatch.setattr(r, "get_run", AsyncMock(return_value=run))
    matter = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(r, "matter_for_user", AsyncMock(return_value=matter))
    db.scalar.return_value = SimpleNamespace(id=uuid.uuid4())
    mapping = dict(
        group="Smith", matter_name="Case", first_name="Jane", last_name="Smith"
    )
    if mode == "existing":
        mapping["matter_id"] = matter.id
    if mode == "contact":
        mapping["contact_id"] = uuid.uuid4()
    if mode == "organization":
        mapping["organization_name"] = "Acme"
    if mode == "exclude":
        mapping["exclude"] = True
    body = r.Approval(confirm=True, mappings=[mapping])
    result = await r.approve(run.id, body, db, user)
    assert result["status"] == "uploading"
    count = db.add.call_count
    assert await r.approve(run.id, body, db, user) == result
    assert db.add.call_count == count
    with pytest.raises(HTTPException):
        await r.approve(
            run.id,
            r.Approval(confirm=True, mappings=[dict(mapping, intake="existing")]),
            db,
            user,
        )


@pytest.mark.asyncio
async def test_invalid_approval_groups_and_contact(ctx, monkeypatch):
    db, user = ctx
    run = run_for(user)
    monkeypatch.setattr(r, "get_run", AsyncMock(return_value=run))
    with pytest.raises(HTTPException):
        await r.approve(
            run.id,
            r.Approval(confirm=True, mappings=[{"group": "wrong", "exclude": True}]),
            db,
            user,
        )
    db.scalar.return_value = None
    with pytest.raises(HTTPException):
        await r.approve(
            run.id,
            r.Approval(
                confirm=True,
                mappings=[
                    {"group": "Smith", "matter_name": "x", "contact_id": uuid.uuid4()}
                ],
            ),
            db,
            user,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["onedrive", "google_drive", "sharepoint"])
@pytest.mark.parametrize("email", [True, False])
async def test_ingest_routes_files_and_emails_without_sending(
    ctx, monkeypatch, provider, email
):
    @asynccontextmanager
    async def storage_session():
        yield AsyncMock()

    monkeypatch.setattr(r, "async_session_maker", storage_session)
    db, user = ctx
    path, content = ("Smith/mail.eml", EML) if email else ("Smith/case.txt", b"case")
    run = run_for(user, path, content)
    run.status = "uploading"
    matter = SimpleNamespace(
        id=uuid.uuid4(),
        slug="smith",
        cloud_folder={provider: {"matter_folder_id": "folder"}},
    )
    run.manifest.update(
        destinations={"Smith": str(matter.id)},
        approval={"former_addresses": []},
        results={},
    )
    monkeypatch.setattr(r, "get_run", AsyncMock(return_value=run))
    monkeypatch.setattr(r, "matter_for_user", AsyncMock(return_value=matter))
    db.scalar.return_value = None
    folder = SimpleNamespace(id=uuid.uuid4(), kind="user", path_segments=["Smith"])
    monkeypatch.setattr(r, "create_folder", AsyncMock(return_value=folder))
    store = AsyncMock(
        return_value=SimpleNamespace(
            succeeded=True,
            storage_path="stored",
            provider=provider,
            backend=provider,
            provider_item_id="item",
            drive_id="drive",
            parent_id="parent",
        )
    )
    monkeypatch.setattr(r.MatterFileStore, "store_matter_file_result", store)
    result = await r.ingest(db, user, run.id, path, content)
    assert result["status"] == "imported" and run.status == "complete"
    assert store.call_args.kwargs["content"] == content
    assert store.call_args.kwargs["matter_cloud_folder"] == matter.cloud_folder
    assert store.call_args.kwargs["folder_path"] == ["Smith"]
    logs = [
        c.args[0]
        for c in db.add.call_args_list
        if isinstance(c.args[0], r.CommunicationLog)
    ]
    assert len(logs) == int(email)
    if logs:
        assert logs[0].status == "logged" and logs[0].direction == "unknown"
    assert await r.ingest(db, user, run.id, path, content) == result
    assert store.call_count == 1
    with pytest.raises(HTTPException):
        await r.attempt_file(db, user, run.id, path, b"changed")
    assert run.manifest["results"][path] == result


@pytest.mark.asyncio
async def test_dedup_exclusion_and_storage_failure(ctx, monkeypatch):
    db, user = ctx
    run = run_for(user)
    run.status = "uploading"
    run.manifest.update(destinations={"Smith": None}, results={})
    monkeypatch.setattr(r, "get_run", AsyncMock(return_value=run))
    assert (await r.ingest(db, user, run.id, "Smith/mail.eml", EML))[
        "status"
    ] == "excluded"
    run.manifest["results"] = {}
    run.manifest["destinations"]["Smith"] = str(uuid.uuid4())
    monkeypatch.setattr(
        r, "matter_for_user", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    )
    db.scalar.return_value = SimpleNamespace(target_record_id=uuid.uuid4())
    assert (await r.ingest(db, user, run.id, "Smith/mail.eml", EML))[
        "status"
    ] == "duplicate"
    monkeypatch.setattr(
        r, "ingest", AsyncMock(side_effect=RuntimeError("private provider failure"))
    )
    result = await r.attempt_file(db, user, run.id, "Smith/mail.eml", EML)
    assert result["status"] == "failed" and "private" not in result["error"]
    assert db.rollback.await_count == 1


@pytest.mark.asyncio
async def test_zip_endpoints(ctx, monkeypatch):
    db, user = ctx
    payload = zipped([("Smith/mail.eml", EML)])
    result = await r.zip_preview(UploadFile(io.BytesIO(payload)), user)
    assert (
        result["files"][0]["sha256"] == file_manifest("Smith/mail.eml", EML)["sha256"]
    )
    run = run_for(user)
    monkeypatch.setattr(r, "get_run", AsyncMock(return_value=run))
    monkeypatch.setattr(
        r, "attempt_file", AsyncMock(return_value={"status": "imported"})
    )
    await r.upload_zip(run.id, UploadFile(io.BytesIO(payload)), db, user)
    assert r.attempt_file.await_count == 1
    await r.upload(run.id, "Smith/mail.eml", UploadFile(io.BytesIO(EML)), db, user)
    assert r.attempt_file.await_count == 2
    assert await r.status(run.id, db, user) == r.response(run)
    with pytest.raises(HTTPException):
        await r.zip_preview(UploadFile(io.BytesIO(b"bad zip")), user)
