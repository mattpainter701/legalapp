import base64
import uuid
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app.services import matter_mail_attachments as attachments
from app.services import matter_panel_visibility as panels
from app.services.connected_mail import _gmail_message, _send_microsoft
from app.services.mail_attachment import MailAttachment


@pytest.mark.parametrize(
    "value,expected",
    [(None, []), ([], []), (["chat", "chat", "team"], ["chat", "team"])],
)
def test_panel_policy_normalizes(value, expected):
    assert panels.validate_hidden_panels(value) == expected


@pytest.mark.parametrize(
    "value", [["dashboard"], ["settings"], ["other"], "chat", [None]]
)
def test_panel_policy_rejects_unknown_and_mandatory_panels(value):
    with pytest.raises(ValueError):
        panels.validate_hidden_panels(value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config,expected",
    [
        (None, []),
        ("legacy", []),
        ({"hidden_matter_panels": ["chat"]}, ["chat"]),
        ({"hidden_matter_panels": ["unknown"]}, []),
    ],
)
async def test_panel_resolution(config, expected):
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(custom_config=config))
    )
    assert await panels.hidden_matter_panels(db, uuid.uuid4()) == expected


@pytest.mark.asyncio
async def test_review_and_send_rejects_changed_bytes_and_preserves_exact_content(
    monkeypatch,
):
    doc = SimpleNamespace(filename="agreement.pdf", content_type="application/pdf")
    db = SimpleNamespace(scalar=AsyncMock(return_value=doc))
    monkeypatch.setattr(
        attachments, "assert_no_legacy_assistant_derivative_release", AsyncMock()
    )
    reader = AsyncMock(return_value=b"%PDF-reviewed")
    monkeypatch.setattr(
        attachments,
        "MatterFileStore",
        lambda: SimpleNamespace(read_matter_file_bytes=reader),
    )
    tid, mid, did = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    item, digest = await attachments.reviewed_attachment(db, tid, mid, did)
    result = await attachments.collect_reviewed_attachments(
        db, tid, mid, [{"document_id": str(did), "sha256": digest}]
    )
    assert result[0].content == item.content == b"%PDF-reviewed"
    reader.return_value = b"%PDF-changed"
    with pytest.raises(HTTPException) as exc:
        await attachments.reviewed_attachment(db, tid, mid, did, digest)
    assert exc.value.status_code == 412


@pytest.mark.asyncio
@pytest.mark.parametrize("selections", [None, [{}] * 11, [{}], [{"sha256": "x"}]])
async def test_attachment_manifest_requires_review(selections):
    with pytest.raises(HTTPException) as exc:
        await attachments.collect_reviewed_attachments(
            None, uuid.uuid4(), uuid.uuid4(), selections
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_attachment_duplicate_and_aggregate_bound(monkeypatch):
    monkeypatch.setattr(
        attachments,
        "reviewed_attachment",
        AsyncMock(
            return_value=(
                MailAttachment("a.pdf", b"x" * (1024 * 1024 + 1), "application/pdf"),
                "a" * 64,
            )
        ),
    )
    item = {"document_id": "one", "sha256": "a" * 64}
    with pytest.raises(HTTPException) as exc:
        await attachments.collect_reviewed_attachments(None, None, None, [item, item])
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await attachments.collect_reviewed_attachments(
            None, None, None, [item, {**item, "document_id": "two"}]
        )
    assert exc.value.status_code == 413


def test_gmail_keeps_multiple_attachments_separate():
    files = [
        MailAttachment("agreement.pdf", b"fee", "application/pdf"),
        MailAttachment("intake.txt", b"questions", "text/plain"),
    ]
    raw = _gmail_message(
        to=["jane@example.test"],
        subject="Paperwork",
        html_body="<p>Review</p>",
        text_body="Review",
        attachments=files,
    )
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw)
    )
    assert [
        (part.get_filename(), part.get_payload(decode=True))
        for part in message.iter_attachments()
    ] == [("agreement.pdf", b"fee"), ("intake.txt", b"questions")]


@pytest.mark.asyncio
async def test_microsoft_sends_each_attachment(monkeypatch):
    from app.services import connected_mail

    send = AsyncMock(return_value=SimpleNamespace(status_code=202))
    monkeypatch.setattr(connected_mail, "graph_request", send)
    await _send_microsoft(
        "test-token",
        to=["jane@example.test"],
        subject="Paperwork",
        html_body="Review",
        attachments=[
            MailAttachment("a.pdf", b"a", "application/pdf"),
            MailAttachment("b.pdf", b"b", "application/pdf"),
        ],
    )
    assert [
        item["name"] for item in send.call_args.kwargs["json"]["message"]["attachments"]
    ] == ["a.pdf", "b.pdf"]
    assert send.call_args.kwargs["max_retries"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["success", "missing", "replay", "conflict", "storage_failure", "uncertain"]
)
async def test_copy_document_is_private_retryable_and_never_mutates_source(
    monkeypatch, case
):
    from contextlib import asynccontextmanager
    from app.routers import matter_document_folders as routes, matter_documents
    from app.services import matter_file_store, matter_document_revisions
    from app import database
    from app.models.matter_document import MatterDocument
    from app.services.matter_file_store import StorageResult

    tid, mid, uid, source_id, copy_id, folder_id = [uuid.uuid4() for _ in range(6)]
    source = MatterDocument(
        id=source_id,
        tenant_id=tid,
        matter_id=mid,
        filename="Jane.pdf",
        storage_path="source-path",
        folder_id=None,
        portal_visible=True,
        content_type="application/pdf",
        positioned_fields=[],
        signing_placement_required=False,
    )
    copied = MatterDocument(
        id=copy_id,
        tenant_id=tid,
        matter_id=mid,
        folder_id=folder_id,
        description=f"Copy of document {source_id}",
    )
    if case == "conflict":
        copied.folder_id = None
    db = SimpleNamespace(
        scalar=AsyncMock(
            side_effect=[
                mid,
                None if case == "missing" else source,
                copied if case in {"replay", "conflict"} else None,
            ]
        ),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        add=lambda row: added.append(row),
    )
    added = []
    if case == "uncertain":
        db.commit.side_effect = RuntimeError("lost acknowledgement")
    monkeypatch.setattr(
        routes,
        "get_current_user",
        AsyncMock(return_value=SimpleNamespace(id=uid, tenant_id=tid)),
    )
    monkeypatch.setattr(routes, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        routes,
        "_get_matter_or_404",
        AsyncMock(return_value=SimpleNamespace(slug="jane", cloud_folder=None)),
    )
    monkeypatch.setattr(
        routes,
        "get_folder_or_404",
        AsyncMock(
            return_value=SimpleNamespace(
                kind="user", path="Forms", path_segments=["Forms"], system_key=None
            )
        ),
    )
    monkeypatch.setattr(
        matter_document_revisions,
        "assert_no_legacy_assistant_derivative_release",
        AsyncMock(),
    )
    monkeypatch.setattr(
        matter_documents,
        "serialize_document",
        AsyncMock(side_effect=lambda db, **kwargs: kwargs["document"]),
    )

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(database, "async_session_maker", session)
    writer = AsyncMock(
        return_value=StorageResult(
            provider="local",
            backend="local",
            storage_path="copy-path",
            error="offline" if case == "storage_failure" else None,
        )
    )
    remover = AsyncMock()
    monkeypatch.setattr(
        matter_file_store,
        "MatterFileStore",
        lambda: SimpleNamespace(
            read_matter_file_bytes=AsyncMock(return_value=b"%PDF-copy"),
            store_matter_file_result=writer,
            delete_stored_result=remover,
        ),
    )
    body = routes.DocumentCopyRequest(
        document_id=source_id, copy_id=copy_id, folder_id=folder_id
    )
    expected = {
        "missing": 404,
        "conflict": 409,
        "storage_failure": 502,
        "uncertain": 503,
    }
    if case in expected:
        with pytest.raises(HTTPException) as error:
            await routes.copy_matter_document(str(mid), body, None, db)
        assert error.value.status_code == expected[case]
    else:
        result = await routes.copy_matter_document(str(mid), body, None, db)
        assert result.id == copy_id
        if case == "success":
            assert result.portal_visible is False and result.folder_id == folder_id
            assert result.storage_path == "copy-path" and result.file_size == 9
            assert copy_id.hex[:12] in writer.call_args.kwargs["filename"]
            assert writer.call_args.kwargs["folder_path"] == ["Forms"]
        else:
            writer.assert_not_called()
    assert (
        source.storage_path == "source-path"
        and source.folder_id is None
        and source.portal_visible
    )
    remover.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["bad-id", str(uuid.uuid4())])
async def test_attachment_missing_or_invalid_is_not_disclosed(value):
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await attachments.reviewed_attachment(db, uuid.uuid4(), uuid.uuid4(), value)
    assert error.value.status_code in {404, 422}


@pytest.mark.asyncio
async def test_attachment_read_failure_and_release_guard(monkeypatch):
    from app.services.matter_document_revisions import DocumentRevisionServiceError

    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                filename="a.pdf", content_type="application/pdf"
            )
        )
    )
    guard = AsyncMock()
    monkeypatch.setattr(
        attachments, "assert_no_legacy_assistant_derivative_release", guard
    )
    monkeypatch.setattr(
        attachments,
        "MatterFileStore",
        lambda: SimpleNamespace(
            read_matter_file_bytes=AsyncMock(side_effect=RuntimeError("unavailable"))
        ),
    )
    with pytest.raises(HTTPException) as error:
        await attachments.reviewed_attachment(
            db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        )
    assert error.value.status_code == 422
    guard.side_effect = DocumentRevisionServiceError(409, "locked", "Review required")
    with pytest.raises(HTTPException) as error:
        await attachments.reviewed_attachment(
            db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        )
    assert error.value.status_code == 409
