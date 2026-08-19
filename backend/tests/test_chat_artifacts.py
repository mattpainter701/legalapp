import uuid
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest
from httpx import AsyncClient

from app.models.plugin import Matter
from app.routers import chat_artifacts as chat_artifacts_router
from app.routers.chat_artifacts import _requested_filename
from app.schemas.chat_artifact import ChatArtifactCreate
from app.services.artifact_extraction import extract_artifacts, strip_artifacts
from app.services.document_export import markdown_to_docx_bytes, markdown_to_pdf_bytes
from app.services.matter_file_store import StorageResult


def test_extract_and_strip_artifact_blocks() -> None:
    response = '''Here is the requested revision.

:::artifact title="Mutual NDA - Section 3"
## Confidentiality

The parties must protect confidential information.
:::

Please have counsel review before sending.'''

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
        f':::artifact title="Draft {index}"\nBody {index}\n:::'
        for index in range(4)
    )
    oversized = (
        ':::artifact title="Oversized"\n'
        + ("x" * 200_001)
        + "\n:::"
    )

    assert extract_artifacts(too_many) == []
    assert extract_artifacts(oversized) == []
    assert strip_artifacts(too_many) == too_many
    assert strip_artifacts(oversized) == oversized


def test_artifact_payload_is_bounded_and_markdown_only() -> None:
    assert ChatArtifactCreate(title="Draft", content="Body").format == "markdown"
    assert ChatArtifactCreate(title="  Draft  ", content="  indented body\n").title == "Draft"
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
