from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.generated_artifact import (
    GeneratedArtifact,
    GeneratedArtifactRevision,
)
from app.schemas.chat_action import MatterDocumentDraftAction
from app.services.generated_artifacts import (
    ALLOWED_ARTIFACT_CHANNELS,
    GeneratedArtifactError,
    canonical_artifact_request_sha256,
    create_generated_artifact_revision,
    derive_artifact_request_id,
)


class RevisionDB:
    def __init__(self, artifact, parent):
        self.values = iter((artifact, parent))
        self.added = []
        self.flushes = 0

    async def scalar(self, _statement):
        return next(self.values)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1


def test_request_fingerprint_is_canonical_and_content_sensitive():
    first = canonical_artifact_request_sha256(
        {"matter_id": "m1", "title": "Motion", "body": "Draft"}
    )
    reordered = canonical_artifact_request_sha256(
        {"body": "Draft", "title": "Motion", "matter_id": "m1"}
    )
    changed = canonical_artifact_request_sha256(
        {"matter_id": "m1", "title": "Motion", "body": "Changed"}
    )

    assert first == reordered
    assert first != changed
    assert len(first) == 64


def test_transport_idempotency_key_derives_a_stable_tenant_bound_uuid():
    tenant_id = uuid4()

    first = derive_artifact_request_id(
        tenant_id=tenant_id,
        channel="workspace_mcp",
        explicit_request_id=None,
        transport_request_id="desktop-request-1",
    )
    second = derive_artifact_request_id(
        tenant_id=tenant_id,
        channel="workspace_mcp",
        explicit_request_id=None,
        transport_request_id="desktop-request-1",
    )
    other_tenant = derive_artifact_request_id(
        tenant_id=uuid4(),
        channel="workspace_mcp",
        explicit_request_id=None,
        transport_request_id="desktop-request-1",
    )

    assert first == second
    assert first != other_tenant


def test_explicit_client_request_id_is_preserved():
    request_id = uuid4()

    resolved = derive_artifact_request_id(
        tenant_id=uuid4(),
        channel="matter_chat",
        explicit_request_id=request_id,
        transport_request_id="ignored",
    )

    assert resolved == request_id


def test_pending_action_requires_artifact_and_cloud_document_bindings():
    base = {
        "type": "matter_document_draft",
        "matter_id": str(uuid4()),
        "title": "Draft motion",
        "body": "Draft body",
    }
    legacy = MatterDocumentDraftAction.model_validate(base)
    bound = MatterDocumentDraftAction.model_validate(
        {
            **base,
            "artifact_id": str(uuid4()),
            "artifact_revision_id": str(uuid4()),
            "artifact_revision_no": 1,
            "artifact_sha256": "a" * 64,
            "document_id": str(uuid4()),
            "document_sha256": "b" * 64,
            "document_storage_backend": "sharepoint",
            "document_provider_etag": '"etag-1"',
        }
    )

    assert legacy.artifact_id is None
    assert bound.artifact_revision_no == 1
    assert bound.document_storage_backend == "sharepoint"
    with pytest.raises(ValidationError, match="binding must be complete"):
        MatterDocumentDraftAction.model_validate({**base, "artifact_id": str(uuid4())})
    with pytest.raises(ValidationError, match="tenant-cloud document"):
        MatterDocumentDraftAction.model_validate(
            {
                **base,
                "artifact_id": str(uuid4()),
                "artifact_revision_id": str(uuid4()),
                "artifact_revision_no": 1,
                "artifact_sha256": "a" * 64,
            }
        )


def _foreign_key(table, name):
    return next(item for item in table.foreign_key_constraints if item.name == name)


def test_database_constraints_bind_revisions_to_tenant_and_artifact():
    artifact_table = GeneratedArtifact.__table__
    revision_table = GeneratedArtifactRevision.__table__

    current = _foreign_key(artifact_table, "fk_generated_artifacts_current_revision")
    assert [column.name for column in current.columns] == [
        "tenant_id",
        "id",
        "current_revision_no",
    ]
    assert [element.target_fullname for element in current.elements] == [
        "generated_artifact_revisions.tenant_id",
        "generated_artifact_revisions.artifact_id",
        "generated_artifact_revisions.revision_no",
    ]
    assert current.deferrable is True
    assert current.initially == "DEFERRED"

    tenant_artifact = _foreign_key(
        revision_table, "fk_generated_artifact_revisions_tenant_artifact"
    )
    assert [column.name for column in tenant_artifact.columns] == [
        "tenant_id",
        "artifact_id",
    ]
    assert [element.target_fullname for element in tenant_artifact.elements] == [
        "generated_artifacts.tenant_id",
        "generated_artifacts.id",
    ]

    parent = _foreign_key(revision_table, "fk_generated_artifact_revisions_parent")
    assert [column.name for column in parent.columns] == [
        "tenant_id",
        "artifact_id",
        "parent_revision_id",
    ]
    assert [element.target_fullname for element in parent.elements] == [
        "generated_artifact_revisions.tenant_id",
        "generated_artifact_revisions.artifact_id",
        "generated_artifact_revisions.id",
    ]


def test_database_and_service_bound_external_artifact_metadata():
    artifact_constraints = {
        item.name for item in GeneratedArtifact.__table__.constraints
    }
    revision_constraints = {
        item.name for item in GeneratedArtifactRevision.__table__.constraints
    }

    assert ALLOWED_ARTIFACT_CHANNELS == {"matter_chat", "workspace_mcp"}
    assert "ck_generated_artifacts_source_channel" in artifact_constraints
    assert "ck_generated_artifacts_format" in artifact_constraints
    assert "ck_generated_artifacts_request_sha256" in artifact_constraints
    assert "ck_generated_artifact_revisions_content_sha256" in revision_constraints
    assert "ck_generated_artifact_revisions_template_sha256" in revision_constraints


def test_artifact_revision_migration_blocks_in_place_updates():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "114_generated_artifacts.py"
    ).read_text(encoding="utf-8")

    assert "generated_artifact_revisions_immutable" in migration
    assert "BEFORE UPDATE ON generated_artifact_revisions" in migration
    assert "law_hand_reject_generated_artifact_revision_update" in migration


@pytest.mark.asyncio
async def test_edit_appends_a_revision_without_mutating_the_parent():
    tenant_id = uuid4()
    artifact_id = uuid4()
    parent_id = uuid4()
    artifact = SimpleNamespace(
        id=artifact_id,
        tenant_id=tenant_id,
        status="review",
        title="Draft motion",
        current_revision_no=1,
    )
    parent = SimpleNamespace(
        id=parent_id,
        template_id=None,
        template_sha256=None,
        template_format=None,
        variable_snapshot={"matter_name": "Example"},
        unresolved_variables=[],
        source_snapshot=[{"source_id": "authority-1"}],
        renderer_version="renderer-v1",
        model_metadata=None,
        content_text="Original body",
        revision_no=1,
    )
    db = RevisionDB(artifact, parent)

    revision = await create_generated_artifact_revision(
        db,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        actor_user_id=uuid4(),
        expected_revision_no=1,
        content_text="Revised body",
        title="Revised motion",
    )

    assert parent.content_text == "Original body"
    assert revision.parent_revision_id == parent_id
    assert revision.revision_no == 2
    assert revision.content_text == "Revised body"
    assert revision.source_snapshot == parent.source_snapshot
    assert artifact.current_revision_no == 2
    assert artifact.title == "Revised motion"
    assert db.added == [revision]
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_edit_fails_on_a_stale_artifact_revision():
    artifact = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        status="review",
        title="Draft",
        current_revision_no=2,
    )
    db = RevisionDB(artifact, None)

    with pytest.raises(GeneratedArtifactError) as caught:
        await create_generated_artifact_revision(
            db,
            tenant_id=artifact.tenant_id,
            artifact_id=artifact.id,
            actor_user_id=uuid4(),
            expected_revision_no=1,
            content_text="Stale edit",
        )

    assert caught.value.code == "artifact_version_conflict"
    assert db.added == []
