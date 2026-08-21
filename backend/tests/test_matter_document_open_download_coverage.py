from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.routers.matter_documents as routes
from app.services.matter_file_store import (
    MatterFileIntegrityError,
    MatterFileNotFound,
    MatterFileReadError,
)


class _DB:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _document(**overrides):
    values = {
        "id": uuid4(),
        "matter_id": uuid4(),
        "task_id": uuid4(),
        "generated_artifact_id": uuid4(),
        "generated_artifact_revision_id": uuid4(),
        "storage_backend": "google_drive",
        "storage_state": "verified",
        "storage_error": None,
        "storage_path": None,
        "document_sha256": "a" * 64,
        "file_size": 3,
        "content_type": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "filename": "draft & review.docx",
        "provider_object_id": "provider-object",
        "provider_etag": '"etag"',
        "provider_version_id": "v1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_request_context(monkeypatch, document):
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    calls = {"tenant": [], "lookup": []}

    async def current_user(request, db):
        assert request is not None
        assert db is not None
        return user

    async def tenant_context(db, tenant_id):
        calls["tenant"].append((db, tenant_id))

    async def get_document(doc_id, matter_id, tenant_id, db):
        calls["lookup"].append((doc_id, matter_id, tenant_id, db))
        return document

    monkeypatch.setattr(routes, "get_current_user", current_user)
    monkeypatch.setattr(routes, "set_tenant_context", tenant_context)
    monkeypatch.setattr(routes, "_get_doc_or_404", get_document)
    return user, calls


@pytest.mark.asyncio
async def test_open_matter_document_resolves_fresh_provider_url(monkeypatch):
    document = _document()
    user, calls = _install_request_context(monkeypatch, document)
    db = _DB()
    observed = {}

    async def open_url(**kwargs):
        observed.update(kwargs)
        return "https://drive.google.com/file/d/provider-object/view"

    monkeypatch.setattr(routes.matter_file_store, "get_matter_file_open_url", open_url)

    response = await routes.open_matter_document(
        matter_id=str(document.matter_id),
        doc_id=str(document.id),
        request=object(),
        db=db,
    )

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://drive.google.com/")
    assert response.headers["cache-control"] == "no-store"
    assert observed == {
        "db": db,
        "tenant_id": str(user.tenant_id),
        "document": document,
    }
    assert calls["tenant"] == [(db, str(user.tenant_id))]
    assert calls["lookup"] == [
        (str(document.id), str(document.matter_id), user.tenant_id, db)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (MatterFileNotFound("gone"), 404, "Cloud document not found"),
        (
            MatterFileReadError("provider detail"),
            409,
            "The cloud document cannot be opened until its binding is repaired",
        ),
    ],
)
async def test_open_matter_document_maps_provider_failures(
    monkeypatch, error, status_code, detail
):
    document = _document()
    _install_request_context(monkeypatch, document)

    async def fail(**_kwargs):
        raise error

    monkeypatch.setattr(routes.matter_file_store, "get_matter_file_open_url", fail)

    with pytest.raises(HTTPException) as exc_info:
        await routes.open_matter_document(
            matter_id=str(document.matter_id),
            doc_id=str(document.id),
            request=object(),
            db=_DB(),
        )

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == detail


@pytest.mark.asyncio
async def test_download_matter_document_returns_verified_registered_bytes(monkeypatch):
    document = _document()
    user, _calls = _install_request_context(monkeypatch, document)
    db = _DB()
    observed = {}

    async def read_bytes(**kwargs):
        observed.update(kwargs)
        return b"doc"

    monkeypatch.setattr(routes.matter_file_store, "read_matter_file_bytes", read_bytes)

    response = await routes.download_matter_document(
        matter_id=str(document.matter_id),
        doc_id=str(document.id),
        request=object(),
        db=db,
    )

    assert response.status_code == 200
    assert response.body == b"doc"
    assert response.media_type == document.content_type
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "draft%20%26%20review.docx" in response.headers["content-disposition"]
    assert observed == {
        "db": db,
        "tenant_id": str(user.tenant_id),
        "document": document,
        "expected_sha256": document.document_sha256,
        "expected_size": document.file_size,
    }


@pytest.mark.asyncio
async def test_download_matter_document_blocks_known_cloud_conflict(monkeypatch):
    document = _document(storage_state="conflict")
    _install_request_context(monkeypatch, document)

    async def unexpected_read(**_kwargs):
        raise AssertionError("conflicted cloud bytes must not be read")

    monkeypatch.setattr(
        routes.matter_file_store, "read_matter_file_bytes", unexpected_read
    )

    with pytest.raises(HTTPException) as exc_info:
        await routes.download_matter_document(
            matter_id=str(document.matter_id),
            doc_id=str(document.id),
            request=object(),
            db=_DB(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == (
        "This cloud document must be reconciled before download"
    )


@pytest.mark.asyncio
async def test_download_integrity_failure_records_conflict_before_returning(
    monkeypatch,
):
    document = _document()
    user, _calls = _install_request_context(monkeypatch, document)
    db = _DB()
    events = []

    async def bad_bytes(**_kwargs):
        raise MatterFileIntegrityError("hash mismatch")

    async def append_event(*args, **kwargs):
        events.append((args, kwargs))

    monkeypatch.setattr(routes.matter_file_store, "read_matter_file_bytes", bad_bytes)
    monkeypatch.setattr(routes, "append_document_integrity_event", append_event)

    with pytest.raises(HTTPException) as exc_info:
        await routes.download_matter_document(
            matter_id=str(document.matter_id),
            doc_id=str(document.id),
            request=object(),
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == (
        "The cloud document changed and must be reconciled"
    )
    assert document.storage_state == "conflict"
    assert "reconciliation is required" in document.storage_error
    assert db.commits == 1
    assert len(events) == 1
    args, event = events[0]
    assert args == (db,)
    assert event["tenant_id"] == user.tenant_id
    assert event["event_type"] == "cloud_download_integrity_conflict"
    assert event["actor_user_id"] == user.id
    assert event["provider_object_id"] == document.provider_object_id
    assert event["metadata"] == {"storage_backend": "google_drive"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (MatterFileNotFound("gone"), 404, "Cloud document not found"),
        (
            MatterFileReadError("provider detail"),
            503,
            "The cloud document is temporarily unavailable",
        ),
    ],
)
async def test_download_matter_document_maps_cloud_read_failures(
    monkeypatch, error, status_code, detail
):
    document = _document(storage_state="pending")
    _install_request_context(monkeypatch, document)
    observed = {}

    async def fail(**kwargs):
        observed.update(kwargs)
        raise error

    monkeypatch.setattr(routes.matter_file_store, "read_matter_file_bytes", fail)

    with pytest.raises(HTTPException) as exc_info:
        await routes.download_matter_document(
            matter_id=str(document.matter_id),
            doc_id=str(document.id),
            request=object(),
            db=_DB(),
        )

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == detail
    assert observed["expected_sha256"] is None
