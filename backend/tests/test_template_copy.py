"""Template variations own their files and never inherit publication evidence."""

import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.document_template import DocumentTemplate
from app.schemas.document_template import DocumentTemplateCopyRequest


@pytest.fixture
def copy_context(monkeypatch, tmp_path):
    from app.routers import document_templates as router

    tenant = uuid.uuid4()
    template = DocumentTemplate(
        id=uuid.uuid4(),
        tenant_id=tenant,
        title="Master",
        body="{{client}}",
        format="docx",
        source_filename="master.docx",
        source_storage_path="master",
        source_sha256=hashlib.sha256(b"active").hexdigest(),
        source_content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_provenance={"original_template_id": "first-upload"},
        variable_schema={
            "fields": [{"name": "client", "binding": "matter.client_name"}]
        },
        is_active=True,
        status="published",
        current_version_no=7,
        published_version_no=7,
        tested_version_no=7,
    )
    db = AsyncMock()
    db.add = Mock()
    db.scalar.return_value = template
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router, "_verified_template_source", AsyncMock(return_value=b"active")
    )
    monkeypatch.setattr(
        router,
        "_verified_word_original",
        AsyncMock(return_value=(b"original", "first.docx")),
    )
    monkeypatch.setattr(router, "_template_response", lambda value: value)
    monkeypatch.setattr(
        router,
        "_template_source_dir",
        lambda tenant_id, template_id: str(
            tmp_path / str(tenant_id) / str(template_id)
        ),
    )
    return router, template, db, SimpleNamespace(tenant_id=tenant), tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["pdf", "docx", "markdown"])
async def test_copy_owns_source_and_schema_and_clears_release_state(
    copy_context, format
):
    router, master, db, user, root = copy_context
    master.format = format
    if format == "markdown":
        master.source_storage_path = None
    copy = await router.copy_template(
        master.id, DocumentTemplateCopyRequest(title="  New variation  "), user, db
    )
    assert copy.id != master.id
    assert copy.tenant_id == master.tenant_id
    assert copy.title == "New variation"
    assert copy.format == format
    assert (
        copy.is_active,
        copy.status,
        copy.current_version_no,
        copy.tested_version_no,
        copy.published_version_no,
    ) == (False, "draft", 0, None, None)
    copy.variable_schema["fields"][0]["name"] = "new_client"
    assert master.variable_schema["fields"][0]["name"] == "client"
    assert master.published_version_no == 7
    if format != "markdown":
        from pathlib import Path

        assert Path(copy.source_storage_path).read_bytes() == b"active"
        assert Path(copy.source_evidence_storage_path).read_bytes() == b"original"
        assert copy.source_evidence_filename == "first.docx"
        assert str(copy.id) in copy.source_storage_path
        assert copy.source_provenance["parent_template_id"] == str(master.id)
        assert copy.source_provenance["original_template_id"] == "first-upload"
    else:
        router._verified_template_source.assert_not_awaited()
        assert not list(root.rglob("*"))
    query = str(db.scalar.call_args.args[0])
    assert "document_templates.tenant_id =" in query
    router.set_tenant_context.assert_awaited_once_with(db, str(user.tenant_id))
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_foreign_or_missing_template_is_not_copied(copy_context):
    router, master, db, user, root = copy_context
    db.scalar.return_value = None
    with pytest.raises(HTTPException) as error:
        await router.copy_template(
            master.id, DocumentTemplateCopyRequest(title="Copy"), user, db
        )
    assert error.value.status_code == 404
    assert not list(root.rglob("*"))
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_integrity_failure_blocks_copy_before_any_write(copy_context):
    router, master, db, user, root = copy_context
    router._verified_template_source.side_effect = HTTPException(
        409, "Integrity failure"
    )
    with pytest.raises(HTTPException) as error:
        await router.copy_template(
            master.id, DocumentTemplateCopyRequest(title="Copy"), user, db
        )
    assert error.value.status_code == 409
    assert not list(root.rglob("*"))
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["commit", "second_write"])
async def test_failed_copy_removes_only_its_created_files(
    copy_context, monkeypatch, failure
):
    router, master, db, user, root = copy_context
    retained = root / "retained.docx"
    retained.write_bytes(b"keep")
    if failure == "commit":
        db.commit.side_effect = RuntimeError("database unavailable")
    else:
        original = router._persist_template_source

        async def persist(**kwargs):
            if kwargs["filename"].startswith("original-"):
                raise OSError("disk full")
            return await original(**kwargs)

        monkeypatch.setattr(router, "_persist_template_source", persist)
    with pytest.raises(HTTPException) as error:
        await router.copy_template(
            master.id, DocumentTemplateCopyRequest(title="Copy"), user, db
        )
    assert error.value.status_code == 500
    assert [path for path in root.rglob("*") if path.is_file()] == [retained]
    db.rollback.assert_awaited_once()


@pytest.mark.parametrize("title", ["", "   ", "x" * 301])
def test_copy_requires_a_bounded_name(title):
    with pytest.raises(ValidationError):
        DocumentTemplateCopyRequest(title=title)
