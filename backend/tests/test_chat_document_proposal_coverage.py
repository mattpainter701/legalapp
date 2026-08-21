"""Focused coverage for the cloud-backed document proposal chat handler."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.chat_action import ProposeMatterDocumentArgs
from app.services import chat_tools
from app.services.chat_tools import handlers
from app.services.chat_tools.handlers import ChatToolContext
from app.services.cloud_artifact_materialization import (
    CloudArtifactMaterializationError,
)
from app.services.generated_artifacts import GeneratedArtifactError


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _DB:
    def __init__(self, task=None):
        self.task = task
        self.flushes = 0

    def begin_nested(self):
        return _Nested()

    async def flush(self):
        self.flushes += 1

    async def scalar(self, _query):
        return self.task


def _fixture(*, created=True, task=None):
    tenant_id, actor_id, matter_id = uuid4(), uuid4(), uuid4()
    artifact_id, revision_id, document_id, task_id = uuid4(), uuid4(), uuid4(), uuid4()
    digest = "a" * 64
    artifact = SimpleNamespace(
        id=artifact_id,
        title="Demand letter",
        task_id=None if created else task_id,
        status="draft",
    )
    revision = SimpleNamespace(
        id=revision_id,
        revision_no=2,
        content_text="Dear client, this is a draft.",
        content_sha256=digest,
    )
    result = SimpleNamespace(artifact=artifact, revision=revision, created=created)
    task = task or SimpleNamespace(
        id=task_id,
        title="Review document: Demand letter",
        status="review",
        version=1,
        review_policy="staff_then_attorney",
        review_stage="staff",
        staff_reviewer_user_id=uuid4(),
        attorney_reviewer_user_id=uuid4(),
        matter_id=matter_id,
        due_date=date(2026, 9, 1),
        pending_action={
            "artifact_id": str(artifact_id),
            "artifact_revision_id": str(revision_id),
            "artifact_sha256": digest,
        },
    )
    document = SimpleNamespace(
        id=document_id,
        document_sha256="b" * 64,
        storage_backend="google_drive",
        storage_state="verified",
        provider_etag="etag-1",
        provider_version_id="version-1",
    )
    materialized = SimpleNamespace(document=document)
    user = SimpleNamespace(id=actor_id, tenant_id=tenant_id)
    context = ChatToolContext(
        db=_DB(task),
        user=user,
        channel="matter_chat",
        conversation_id=uuid4(),
        request_id="request-123",
    )
    args = ProposeMatterDocumentArgs(
        matter_id=matter_id,
        client_request_id=uuid4(),
        title="Demand letter",
        document_kind="demand_letter",
        body="Dear client, this is a draft.",
        due_date=date(2026, 9, 1),
        source_ids=["source-1"],
    )
    return context, args, result, artifact, revision, task, materialized


def _patch_common(monkeypatch, *, task, reviewers=(uuid4(), uuid4())):
    matter = SimpleNamespace(id=task.matter_id)
    monkeypatch.setattr(handlers, "_require_matter", lambda *_a, **_k: _async(matter))
    monkeypatch.setattr(
        handlers,
        "_resolve_document_reviewers",
        lambda *_a, **_k: _async(reviewers),
    )
    monkeypatch.setattr(
        handlers,
        "_resolve_source_chips",
        lambda *_a, **_k: _async(
            [{"source_id": "source-1", "label": "Evidence", "url": "/api/documents/1"}]
        ),
    )
    return matter, reviewers


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_propose_document_creates_task_materializes_and_returns_contract(
    monkeypatch,
):
    context, args, result, artifact, revision, task, materialized = _fixture()
    _patch_common(monkeypatch, task=task)
    monkeypatch.setattr(handlers, "derive_artifact_request_id", lambda **_k: uuid4())
    monkeypatch.setattr(
        handlers, "create_initial_generated_artifact", lambda *_a, **_k: _async(result)
    )

    async def create_task(*_args, **kwargs):
        task.pending_action = kwargs["pending_action"]
        task.staff_reviewer_user_id = kwargs["staff_reviewer_user_id"]
        task.attorney_reviewer_user_id = kwargs["attorney_reviewer_user_id"]
        return task

    monkeypatch.setattr(handlers, "_create_proposed_task", create_task)
    monkeypatch.setattr(
        handlers.cloud_artifact_materializer,
        "materialize",
        lambda **_k: _async(materialized),
    )

    response = await handlers.propose_matter_document(context, args)

    assert response["idempotent_replay"] is False
    assert response["action_type"] == "matter_document_draft"
    assert response["document_storage_backend"] == "google_drive"
    assert response["document_open_url"].endswith(
        f"/documents/{materialized.document.id}/open"
    )
    assert response["due_date"] == "2026-09-01"
    assert context.db.flushes == 2


@pytest.mark.asyncio
async def test_propose_document_replay_validates_existing_task_and_materializes(
    monkeypatch,
):
    context, args, result, artifact, revision, task, materialized = _fixture(
        created=False
    )
    _patch_common(
        monkeypatch,
        task=task,
        reviewers=(task.staff_reviewer_user_id, task.attorney_reviewer_user_id),
    )
    monkeypatch.setattr(handlers, "derive_artifact_request_id", lambda **_k: uuid4())
    monkeypatch.setattr(
        handlers, "create_initial_generated_artifact", lambda *_a, **_k: _async(result)
    )
    monkeypatch.setattr(
        handlers.cloud_artifact_materializer,
        "materialize",
        lambda **_k: _async(materialized),
    )

    response = await handlers.propose_matter_document(context, args)

    assert response["idempotent_replay"] is True
    assert response["task_id"] == str(task.id)
    assert response["pending_action"]["document_id"] == str(materialized.document.id)


@pytest.mark.asyncio
async def test_propose_document_maps_artifact_and_storage_failures(monkeypatch):
    context, args, result, _artifact, _revision, task, _materialized = _fixture()
    _patch_common(monkeypatch, task=task)
    monkeypatch.setattr(handlers, "derive_artifact_request_id", lambda **_k: uuid4())

    async def artifact_failure(*_a, **_k):
        raise GeneratedArtifactError("artifact_conflict", "duplicate")

    monkeypatch.setattr(handlers, "create_initial_generated_artifact", artifact_failure)
    with pytest.raises(chat_tools.ChatToolError) as exc:
        await handlers.propose_matter_document(context, args)
    assert exc.value.code == "artifact_conflict"

    monkeypatch.setattr(
        handlers, "create_initial_generated_artifact", lambda *_a, **_k: _async(result)
    )

    async def create_task(*_a, **_k):
        return task

    monkeypatch.setattr(handlers, "_create_proposed_task", create_task)

    async def materialize_failure(**_k):
        raise CloudArtifactMaterializationError(
            "storage_write_failed", "provider refused"
        )

    monkeypatch.setattr(
        handlers.cloud_artifact_materializer, "materialize", materialize_failure
    )
    with pytest.raises(chat_tools.ChatToolError) as exc:
        await handlers.propose_matter_document(context, args)
        assert exc.value.code == "cloud_materialization_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pending", "code"),
    [
        ({}, "artifact_incomplete"),
        (
            {"artifact_id": "not-a-uuid", "artifact_revision_id": "bad"},
            "artifact_incomplete",
        ),
        (
            {
                "artifact_id": str(uuid4()),
                "artifact_revision_id": str(uuid4()),
                "artifact_sha256": "a" * 64,
            },
            "artifact_version_conflict",
        ),
    ],
)
async def test_propose_document_replay_rejects_invalid_or_conflicting_binding(
    monkeypatch, pending, code
):
    context, args, result, artifact, revision, task, _materialized = _fixture(
        created=False
    )
    task.pending_action = pending
    _patch_common(monkeypatch, task=task)
    monkeypatch.setattr(handlers, "derive_artifact_request_id", lambda **_k: uuid4())
    monkeypatch.setattr(
        handlers, "create_initial_generated_artifact", lambda *_a, **_k: _async(result)
    )

    with pytest.raises(chat_tools.ChatToolError) as exc:
        await handlers.propose_matter_document(context, args)
    assert exc.value.code == code
