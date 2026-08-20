import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.conversation import Conversation, Message
from app.models.plugin import Matter
from app.models.task import Task
from app.routers import chat as chat_router
from app.routers import chat_artifacts as chat_artifacts_router
from app.routers.chat_artifacts import _requested_filename
from app.schemas.chat_artifact import (
    ChatArtifactCreate,
    ChatArtifactUpdate,
    ExportArtifactRequest,
    SaveArtifactToMatterRequest,
)
from app.services.artifact_extraction import extract_artifacts, strip_artifacts
from app.services.document_export import (
    _inline_html,
    _markdown_blocks,
    markdown_to_docx_bytes,
    markdown_to_pdf_bytes,
)
from app.services.matter_file_store import StorageResult


def test_extract_and_strip_artifact_blocks() -> None:
    response = """Here is the requested revision.

:::artifact title="Mutual NDA - Section 3"
## Confidentiality

The parties must protect confidential information.
:::

Please have counsel review before sending."""

    artifacts = extract_artifacts(response)

    assert len(artifacts) == 1
    assert artifacts[0].title == "Mutual NDA - Section 3"
    assert artifacts[0].content == (
        "## Confidentiality\n\nThe parties must protect confidential information."
    )
    assert strip_artifacts(response) == (
        "Here is the requested revision.\n\nPlease have counsel review before sending."
    )


def test_malformed_artifact_is_not_extracted_or_stripped() -> None:
    response = 'Draft follows:\n:::artifact title="Unfinished"\nNo closing fence'

    assert extract_artifacts(response) == []
    assert strip_artifacts(response) == response


def test_artifact_extraction_fails_closed_instead_of_losing_content() -> None:
    too_many = "\n\n".join(
        f':::artifact title="Draft {index}"\nBody {index}\n:::' for index in range(4)
    )
    oversized = ':::artifact title="Oversized"\n' + ("x" * 200_001) + "\n:::"

    assert extract_artifacts(too_many) == []
    assert extract_artifacts(oversized) == []
    assert strip_artifacts(too_many) == too_many
    assert strip_artifacts(oversized) == oversized


def test_artifact_payload_is_bounded_and_markdown_only() -> None:
    assert ChatArtifactCreate(title="Draft", content="Body").format == "markdown"
    assert (
        ChatArtifactCreate(title="  Draft  ", content="  indented body\n").title
        == "Draft"
    )
    assert (
        ChatArtifactCreate(title="Draft", content="  indented body\n").content
        == "  indented body\n"
    )

    with pytest.raises(ValueError):
        ChatArtifactCreate(title="Draft", content="Body", format="html")
    with pytest.raises(ValueError):
        ChatArtifactCreate(title="Draft", content="x" * 200_001)
    with pytest.raises(ValueError):
        ChatArtifactCreate(title="   ", content="Body")
    with pytest.raises(ValueError):
        ChatArtifactCreate(title="Draft", content="   ")


@pytest.mark.parametrize(
    "filename",
    ["../escape.md", "folder\\escape.md", ".", "a\x00b.md", "a\r\nb.md"],
)
def test_artifact_save_filename_must_be_a_single_filename(filename: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _requested_filename(filename, "Draft")

    assert exc.value.status_code == 422


def test_document_exports_are_real_files() -> None:
    markdown = "# Review memo\n\n**Issue:** Notice period.\n\n- Revise section 4"

    assert markdown_to_pdf_bytes(markdown, title="Review memo").startswith(b"%PDF-")
    assert markdown_to_docx_bytes(markdown, title="Review memo").startswith(b"PK")


def test_document_exports_support_all_declared_markdown_blocks() -> None:
    markdown = """## Terms

Plain **bold**, *italic*, and `code` text.

> First quoted line
> Second quoted line

- Alpha
- Beta

1. First
2. Second

| Name | Value |
| --- | --- |
| Notice | 30 days |
"""

    blocks = _markdown_blocks(markdown)
    assert [kind for kind, _ in blocks] == [
        "heading",
        "para",
        "quote",
        "bullets",
        "numbers",
        "table",
    ]
    assert _inline_html("A & B < C **bold** *italic* `code`") == (
        "A &amp; B &lt; C <b>bold</b> <i>italic</i> " '<font face="Courier">code</font>'
    )
    assert markdown_to_pdf_bytes(markdown).startswith(b"%PDF-")
    assert markdown_to_docx_bytes(markdown).startswith(b"PK")


def test_artifact_update_validators_allow_null_and_reject_blank_values() -> None:
    assert ChatArtifactUpdate(title=None, content=None).model_dump() == {
        "title": None,
        "content": None,
        "matter_id": None,
        "task_id": None,
    }
    assert ChatArtifactUpdate(title="  Revised  ").title == "Revised"

    with pytest.raises(ValueError):
        ChatArtifactUpdate(title="   ")
    with pytest.raises(ValueError):
        ChatArtifactUpdate(content="\n\t")


@pytest.mark.asyncio
async def test_message_artifacts_are_persisted_and_extraction_failures_are_safe(
    client: AsyncClient,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    conversation_payload = (await client.post("/api/conversations", json={})).json()
    conversation = await db_session.scalar(
        select(Conversation).where(Conversation.id == conversation_payload["id"])
    )
    message = Message(
        tenant_id=test_user.tenant_id,
        conversation_id=conversation.id,
        role="assistant",
        content="placeholder",
    )
    db_session.add(message)
    await db_session.flush()

    visible, artifacts = await chat_router._persist_message_artifacts(
        db_session,
        user=test_user,
        conv=conversation,
        assistant_msg=message,
        response_text=(
            'Before\n\n:::artifact title="Demand letter"\n'
            "# Demand\n\nPay within 10 days.\n:::\n\nAfter"
        ),
    )

    assert visible == "Before\n\nAfter"
    assert len(artifacts) == 1
    assert artifacts[0].message_id == message.id
    assert artifacts[0].matter_id == conversation.matter_id

    unchanged, none = await chat_router._persist_message_artifacts(
        db_session,
        user=test_user,
        conv=conversation,
        assistant_msg=message,
        response_text="No document block here.",
    )
    assert (unchanged, none) == ("No document block here.", [])

    monkeypatch.setattr(
        chat_router,
        "extract_artifacts",
        lambda _text: (_ for _ in ()).throw(RuntimeError("parser unavailable")),
    )
    unchanged, none = await chat_router._persist_message_artifacts(
        db_session,
        user=test_user,
        conv=conversation,
        assistant_msg=message,
        response_text="Keep this response",
    )
    assert (unchanged, none) == ("Keep this response", [])


@pytest.mark.asyncio
async def test_save_artifact_service_records_storage_metadata_and_compensates(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conversation_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())
    matter_id = uuid.uuid4()
    user = SimpleNamespace(tenant_id=tenant_id, id=user_id)
    matter = SimpleNamespace(
        id=matter_id,
        slug="direct-save",
        cloud_folder=None,
    )
    artifact = SimpleNamespace(
        id=uuid.UUID(artifact_id),
        title="Direct save memo",
        content="# Memo\n\nConfidential.",
        saved_to_matter=False,
        saved_document_id=None,
        matter_id=None,
        task_id=None,
        updated_at=None,
    )
    storage = StorageResult(
        provider="local",
        backend="local",
        storage_path="direct-save.md",
    )
    settings_result = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=settings_result),
        add=MagicMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
    )

    monkeypatch.setattr(
        chat_artifacts_router, "get_current_user", AsyncMock(return_value=user)
    )
    monkeypatch.setattr(chat_artifacts_router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(chat_artifacts_router, "_get_conversation_or_404", AsyncMock())
    monkeypatch.setattr(
        chat_artifacts_router,
        "_get_artifact_or_404",
        AsyncMock(return_value=artifact),
    )
    monkeypatch.setattr(
        chat_artifacts_router,
        "_get_matter_or_404",
        AsyncMock(return_value=matter),
    )
    store = AsyncMock(return_value=storage)
    compensate = AsyncMock()
    monkeypatch.setattr(
        chat_artifacts_router.matter_file_store,
        "store_matter_file_result",
        store,
    )
    monkeypatch.setattr(
        chat_artifacts_router.matter_file_store,
        "delete_stored_result",
        compensate,
    )

    result = await chat_artifacts_router.save_artifact_to_matter(
        conversation_id,
        artifact_id,
        SaveArtifactToMatterRequest(matter_id=matter_id),
        SimpleNamespace(),
        db,
    )

    assert result.artifact_id == artifact.id
    assert result.matter_id == matter_id
    assert result.filename == "Direct-save-memo.md"
    assert result.storage_backend == "local"
    assert artifact.saved_to_matter is True
    assert artifact.saved_document_id == result.document_id
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    compensate.assert_not_awaited()

    artifact.saved_to_matter = False
    artifact.saved_document_id = None
    db.commit.reset_mock(side_effect=True)
    db.commit.side_effect = RuntimeError("database unavailable")

    with pytest.raises(HTTPException) as exc:
        await chat_artifacts_router.save_artifact_to_matter(
            conversation_id,
            artifact_id,
            SaveArtifactToMatterRequest(matter_id=matter_id),
            SimpleNamespace(),
            db,
        )

    assert exc.value.status_code == 500
    db.rollback.assert_awaited_once()
    compensate.assert_awaited_once_with(
        db=db,
        tenant_id=str(tenant_id),
        result=storage,
    )


@pytest.mark.asyncio
async def test_artifact_router_crud_and_export_handlers(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user = SimpleNamespace(tenant_id=tenant_id, id=uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())
    existing = SimpleNamespace(
        id=uuid.UUID(artifact_id),
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        message_id=None,
        created_by_user_id=user.id,
        title="Existing draft",
        content="# Existing",
        format="markdown",
        version=1,
        matter_id=None,
        task_id=None,
        saved_to_matter=False,
        saved_document_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    count_result = SimpleNamespace(scalar_one=lambda: 1)
    rows_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [existing])
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[count_result, rows_result]),
        add=MagicMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        delete=AsyncMock(),
    )

    monkeypatch.setattr(
        chat_artifacts_router, "get_current_user", AsyncMock(return_value=user)
    )
    monkeypatch.setattr(chat_artifacts_router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(chat_artifacts_router, "_get_conversation_or_404", AsyncMock())
    get_artifact = AsyncMock(return_value=existing)
    monkeypatch.setattr(chat_artifacts_router, "_get_artifact_or_404", get_artifact)
    request = SimpleNamespace()

    listed = await chat_artifacts_router.list_artifacts(conversation_id, request, db)
    assert listed.total == 1
    assert listed.items[0].id == existing.id

    created = await chat_artifacts_router.create_artifact(
        conversation_id,
        ChatArtifactCreate(title="  New draft  ", content="# New"),
        request,
        db,
    )
    assert created.title == "New draft"
    assert created.tenant_id == tenant_id
    db.add.assert_called_with(created)

    assert (
        await chat_artifacts_router.get_artifact(
            conversation_id, artifact_id, request, db
        )
        is existing
    )

    updated = await chat_artifacts_router.update_artifact(
        conversation_id,
        artifact_id,
        ChatArtifactUpdate(title="  Updated draft  ", content="# Updated"),
        request,
        db,
    )
    assert updated.title == "Updated draft"
    assert updated.content == "# Updated"
    assert updated.version == 2

    markdown = await chat_artifacts_router.export_artifact(
        conversation_id,
        artifact_id,
        ExportArtifactRequest(format="markdown"),
        request,
        db,
    )
    assert markdown.body == b"# Updated"

    deleted = await chat_artifacts_router.delete_artifact(
        conversation_id, artifact_id, request, db
    )
    assert deleted.status_code == 204
    db.delete.assert_awaited_once_with(existing)


@pytest.mark.asyncio
async def test_artifact_api_round_trip_and_exports(client: AsyncClient) -> None:
    conversation = (await client.post("/api/conversations", json={})).json()
    conversation_id = conversation["id"]

    created_response = await client.post(
        f"/api/conversations/{conversation_id}/artifacts",
        json={
            "title": "  Assignment clause review  ",
            "content": "## Finding\n\nConsent is required.",
        },
    )
    assert created_response.status_code == 201
    created = created_response.json()
    artifact_id = created["id"]
    assert created["title"] == "Assignment clause review"
    assert created["format"] == "markdown"
    assert created["version"] == 1

    listed = await client.get(f"/api/conversations/{conversation_id}/artifacts")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == artifact_id

    updated_response = await client.patch(
        f"/api/conversations/{conversation_id}/artifacts/{artifact_id}",
        json={"content": "## Finding\n\nConsent is **not** required."},
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["version"] == 2

    markdown_export = await client.post(
        f"/api/conversations/{conversation_id}/artifacts/{artifact_id}/export",
        json={"format": "markdown", "filename": "assignment-review.md"},
    )
    assert markdown_export.status_code == 200
    assert markdown_export.text.endswith("Consent is **not** required.")
    assert "assignment-review.md" in markdown_export.headers["content-disposition"]

    pdf_export = await client.post(
        f"/api/conversations/{conversation_id}/artifacts/{artifact_id}/export",
        json={"format": "pdf"},
    )
    assert pdf_export.status_code == 200
    assert pdf_export.content.startswith(b"%PDF-")

    deleted = await client.delete(
        f"/api/conversations/{conversation_id}/artifacts/{artifact_id}"
    )
    assert deleted.status_code == 204
    missing = await client.get(
        f"/api/conversations/{conversation_id}/artifacts/{artifact_id}"
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_artifact_api_rejects_unknown_matter_and_blank_title(
    client: AsyncClient,
) -> None:
    conversation = (await client.post("/api/conversations", json={})).json()
    endpoint = f"/api/conversations/{conversation['id']}/artifacts"

    blank = await client.post(endpoint, json={"title": "   ", "content": "Body"})
    assert blank.status_code == 422

    unknown_matter = await client.post(
        endpoint,
        json={
            "title": "Draft",
            "content": "Body",
            "matter_id": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert unknown_matter.status_code == 404


@pytest.mark.asyncio
async def test_artifact_destination_requires_a_task_from_the_selected_matter(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
) -> None:
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug="artifact-task-target",
        matter_name="Artifact task target",
        matter_type="general",
    )
    other_matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug="artifact-other-target",
        matter_name="Artifact other target",
        matter_type="general",
    )
    task = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        matter_id=matter.id,
        created_by_user_id=test_user.id,
        title="Review generated draft",
    )
    db_session.add_all([matter, other_matter, task])
    await db_session.commit()

    conversation = (await client.post("/api/conversations", json={})).json()
    endpoint = f"/api/conversations/{conversation['id']}/artifacts"

    task_without_matter = await client.post(
        endpoint,
        json={"title": "Draft", "content": "Body", "task_id": str(task.id)},
    )
    assert task_without_matter.status_code == 400

    mismatched_task = await client.post(
        endpoint,
        json={
            "title": "Draft",
            "content": "Body",
            "matter_id": str(other_matter.id),
            "task_id": str(task.id),
        },
    )
    assert mismatched_task.status_code == 400

    created = await client.post(
        endpoint,
        json={
            "title": "  Task-linked draft  ",
            "content": "Initial body",
            "matter_id": str(matter.id),
            "task_id": str(task.id),
        },
    )
    assert created.status_code == 201
    artifact = created.json()
    assert artifact["matter_id"] == str(matter.id)
    assert artifact["task_id"] == str(task.id)

    clear_matter_only = await client.patch(
        f"{endpoint}/{artifact['id']}", json={"matter_id": None}
    )
    assert clear_matter_only.status_code == 400

    updated = await client.patch(
        f"{endpoint}/{artifact['id']}",
        json={"title": "  Revised title  ", "task_id": None, "matter_id": None},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Revised title"
    assert updated.json()["task_id"] is None
    assert updated.json()["matter_id"] is None


@pytest.mark.asyncio
async def test_artifact_save_links_the_created_matter_document(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    monkeypatch,
) -> None:
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug="artifact-save-test",
        matter_name="Artifact save test",
        matter_type="general",
    )
    db_session.add(matter)
    await db_session.commit()

    conversation = (await client.post("/api/conversations", json={})).json()
    created = (
        await client.post(
            f"/api/conversations/{conversation['id']}/artifacts",
            json={"title": "Board consent", "content": "# Board consent"},
        )
    ).json()

    store = AsyncMock(
        return_value=StorageResult(
            provider="local",
            backend="local",
            storage_path="test-artifact.md",
        )
    )
    monkeypatch.setattr(
        chat_artifacts_router.matter_file_store,
        "store_matter_file_result",
        store,
    )

    response = await client.post(
        f"/api/conversations/{conversation['id']}/artifacts/{created['id']}/save",
        json={"matter_id": str(matter.id), "document_category": "generated"},
    )

    assert response.status_code == 200
    saved = response.json()
    assert saved["storage_backend"] == "local"
    assert saved["storage_warning"] is None

    duplicate_save = await client.post(
        f"/api/conversations/{conversation['id']}/artifacts/{created['id']}/save",
        json={"matter_id": str(matter.id), "document_category": "generated"},
    )
    assert duplicate_save.status_code == 409

    artifact = await client.get(
        f"/api/conversations/{conversation['id']}/artifacts/{created['id']}"
    )
    assert artifact.status_code == 200
    assert artifact.json()["saved_to_matter"] is True
    assert artifact.json()["saved_document_id"] == saved["document_id"]

    retarget = await client.patch(
        f"/api/conversations/{conversation['id']}/artifacts/{created['id']}",
        json={"matter_id": None},
    )
    assert retarget.status_code == 409


@pytest.mark.asyncio
async def test_artifact_docx_export_and_invalid_filename_are_rejected(
    client: AsyncClient,
) -> None:
    conversation = (await client.post("/api/conversations", json={})).json()
    created = (
        await client.post(
            f"/api/conversations/{conversation['id']}/artifacts",
            json={"title": "Draft memo", "content": "# Memo\n\nReview `term`."},
        )
    ).json()
    endpoint = (
        f"/api/conversations/{conversation['id']}/artifacts/{created['id']}/export"
    )

    docx_export = await client.post(
        endpoint,
        json={"format": "docx", "filename": "draft-memo.docx"},
    )
    assert docx_export.status_code == 200
    assert docx_export.content.startswith(b"PK")
    assert "draft-memo.docx" in docx_export.headers["content-disposition"]

    invalid_name = await client.post(
        endpoint,
        json={"format": "pdf", "filename": "../outside.pdf"},
    )
    assert invalid_name.status_code == 422
