"""Focused contracts for collaborative research isolation and provenance."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError

from app.models.research_workspace import (
    ResearchRecord,
    ResearchRecordRevision,
    ResearchWorkspace,
    ResearchWorkspaceEvent,
)
from app.routers import research_workspaces as router
from app.schemas.research_workspace import RecordCreate, RecordUpdate, WorkspaceCreate


class Result:
    def __init__(self, row=None, scalar=None, rows=None):
        self.row, self.scalar, self.rows = row, scalar, rows or []

    def one_or_none(self):
        return self.row

    def scalar_one_or_none(self):
        return self.row

    def scalar_one(self):
        return self.scalar

    def scalars(self):
        return self

    def all(self):
        return self.rows


class DB:
    def __init__(self, *results):
        self.results, self.added, self.queries, self.rolled_back = (
            list(results),
            [],
            [],
            False,
        )

    async def execute(self, query):
        self.queries.append(query)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    async def commit(self):
        pass

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, row):
        row.created_at = row.created_at or datetime.now(timezone.utc)
        row.updated_at = row.updated_at or row.created_at


def actor():
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="admin")


@pytest.mark.asyncio
async def test_matter_access_allows_admin_owner_or_assignment_and_denies_unassigned():
    tenant_id, matter_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    matter = SimpleNamespace(id=matter_id, tenant_id=tenant_id, user_id=user_id)
    assert (
        await router._matter(
            matter_id,
            SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="admin"),
            DB(Result(row=matter)),
        )
        is matter
    )
    assert (
        await router._matter(
            matter_id,
            SimpleNamespace(id=user_id, tenant_id=tenant_id, role="user"),
            DB(Result(row=matter)),
        )
        is matter
    )
    assigned_user = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="user")
    assigned_db = DB(Result(row=matter), Result(row=uuid.uuid4()))
    assert await router._matter(matter_id, assigned_user, assigned_db) is matter
    assert "is_active_working IS true" in str(assigned_db.queries[-1])
    with pytest.raises(Exception) as denied:
        await router._matter(
            matter_id,
            SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id, role="user"),
            DB(Result(row=matter), Result(row=None)),
        )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_workspace_creation_makes_the_creator_an_explicit_owner(monkeypatch):
    user, matter_id = actor(), uuid.uuid4()
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "_matter", AsyncMock())
    monkeypatch.setattr(router, "_idempotency_lock", AsyncMock())
    monkeypatch.setattr(router, "_idempotent_response", AsyncMock(return_value=None))
    reservation = SimpleNamespace(response_json=None)
    monkeypatch.setattr(
        router, "_reserve_idempotency", AsyncMock(return_value=reservation)
    )
    db = DB()
    created = await router.create_workspace(
        matter_id, WorkspaceCreate(title="Lease research"), "workspace-key-1", db, user
    )
    assert created["matter_id"] == str(matter_id)
    assert any(
        type(row).__name__ == "ResearchWorkspaceMember"
        and row.user_id == user.id
        and row.role == "owner"
        for row in db.added
    )
    assert any(
        type(row).__name__ == "ResearchWorkspaceEvent"
        and row.action == "workspace_created"
        for row in db.added
    )
    assert created["title"] == "Lease research"


def test_cited_and_excluded_records_cannot_lose_required_provenance():
    with pytest.raises(ValidationError, match="source_url"):
        RecordCreate(record_type="authority", title="Case", evidence_class="cited")
    with pytest.raises(ValidationError, match="exclusion_reason"):
        RecordCreate(record_type="exclusion", title="Not used")
    row = RecordCreate(
        record_type="authority",
        title="Case",
        evidence_class="cited",
        source_url="https://example.test/opinion",
        source_version="v2",
    )
    assert row.evidence_class == "cited"
    assert str(row.source_url) == "https://example.test/opinion"
    with pytest.raises(ValidationError, match="title must not be blank"):
        WorkspaceCreate(title="   ")
    with pytest.raises(ValidationError):
        RecordCreate(record_type="memo", title="Bad state", currentness_state="made_up")
    with pytest.raises(ValidationError):
        RecordCreate(
            record_type="memo", title="Bad treatment", treatment_state="made_up"
        )


def test_research_metadata_matches_critical_tenant_and_retention_contracts():
    workspace_fks = {
        (tuple(fk.column_keys), tuple(col.target_fullname for col in fk.elements))
        for fk in ResearchWorkspace.__table__.foreign_key_constraints
    }
    assert (
        ("tenant_id", "matter_id"),
        ("matters.tenant_id", "matters.id"),
    ) in workspace_fks
    assert not any(
        fk.ondelete == "CASCADE"
        for fk in ResearchWorkspace.__table__.foreign_key_constraints
    )
    event_fks = {
        (tuple(fk.column_keys), tuple(col.target_fullname for col in fk.elements))
        for fk in ResearchWorkspaceEvent.__table__.foreign_key_constraints
    }
    assert (
        ("tenant_id", "workspace_id", "record_id"),
        (
            "research_records.tenant_id",
            "research_records.workspace_id",
            "research_records.id",
        ),
    ) in event_fks
    assert any(
        constraint.name == "ck_research_record_revisions_revision"
        for constraint in ResearchRecordRevision.__table__.constraints
    )


@pytest.mark.asyncio
async def test_stale_record_update_is_rejected_before_overwrite(monkeypatch):
    user, matter_id, workspace_id, record_id = (
        actor(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    record = ResearchRecord(
        id=record_id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="memo",
        title="Old",
        evidence_class="model",
        revision=2,
    )
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router,
        "_workspace",
        AsyncMock(return_value=(workspace, SimpleNamespace(role="editor"))),
    )
    with pytest.raises(Exception) as conflict:
        await router.update_record(
            matter_id,
            workspace_id,
            record_id,
            RecordUpdate(record_type="memo", title="New", revision=1),
            DB(Result(row=record)),
            user,
        )
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_atomic_update_returns_conflict_when_the_compare_and_swap_loses(
    monkeypatch,
):
    user, matter_id, workspace_id, record_id = (
        actor(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    record = ResearchRecord(
        id=record_id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="memo",
        title="Current",
        evidence_class="model",
        revision=2,
    )
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router,
        "_workspace",
        AsyncMock(return_value=(workspace, SimpleNamespace(role="editor"))),
    )
    # The selected version is current, but a concurrent writer wins before the
    # conditional UPDATE. Returning no row must become a 409, never an overwrite.
    with pytest.raises(Exception) as conflict:
        await router.update_record(
            matter_id,
            workspace_id,
            record_id,
            RecordUpdate(record_type="memo", title="Race", revision=2),
            DB(Result(row=record), Result(row=None)),
            user,
        )
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_atomic_archive_returns_conflict_when_an_edit_wins(monkeypatch):
    user, matter_id, workspace_id, record_id = (
        actor(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    record = ResearchRecord(
        id=record_id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="memo",
        title="Current",
        evidence_class="model",
        revision=2,
    )
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router,
        "_workspace",
        AsyncMock(return_value=(workspace, SimpleNamespace(role="editor"))),
    )
    with pytest.raises(Exception) as conflict:
        await router.archive_record(
            matter_id,
            workspace_id,
            record_id,
            DB(Result(row=record), Result(row=None)),
            user,
        )
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_folder_archive_with_active_children_is_a_client_conflict(monkeypatch):
    user, matter_id, workspace_id, record_id = (
        actor(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    folder = ResearchRecord(
        id=record_id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="folder",
        title="Authorities",
        evidence_class="model",
        revision=1,
    )
    db = DB(Result(row=folder), Result(row=uuid.uuid4()))
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router,
        "_workspace",
        AsyncMock(return_value=(workspace, SimpleNamespace(role="editor"))),
    )
    with pytest.raises(Exception) as conflict:
        await router.archive_record(matter_id, workspace_id, record_id, db, user)
    assert conflict.value.status_code == 409
    assert "active child" in conflict.value.detail


@pytest.mark.asyncio
async def test_folder_archive_trigger_race_is_a_client_conflict(monkeypatch):
    user, matter_id, workspace_id, record_id = (
        actor(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    folder = ResearchRecord(
        id=record_id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="folder",
        title="Authorities",
        evidence_class="model",
        revision=1,
    )
    trigger_rejection = DBAPIError.instance(
        "UPDATE",
        {},
        Exception("cannot archive a folder with active records"),
        Exception,
        False,
    )
    db = DB(Result(row=folder), Result(row=None), trigger_rejection)
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router,
        "_workspace",
        AsyncMock(return_value=(workspace, SimpleNamespace(role="editor"))),
    )
    with pytest.raises(Exception) as conflict:
        await router.archive_record(matter_id, workspace_id, record_id, db, user)
    assert conflict.value.status_code == 409
    assert "active child" in conflict.value.detail
    assert db.rolled_back


@pytest.mark.asyncio
async def test_snapshot_payload_preserves_classes_source_and_limitations():
    user, matter_id, workspace_id = actor(), uuid.uuid4(), uuid.uuid4()
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    cited = ResearchRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="authority",
        title="Authority",
        evidence_class="cited",
        source_url="https://example.test/source",
        source_version="2026-08",
        source_as_of=datetime.now(timezone.utc),
        currentness_state="review_needed",
        treatment_state="unknown",
        revision=1,
    )
    model = ResearchRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="memo",
        title="Synthesis",
        evidence_class="model",
        revision=1,
    )
    payload = await router._snapshot_payload(workspace, DB(Result(rows=[cited, model])))
    assert [row["evidence_class"] for row in payload["records"]] == ["cited", "model"]
    assert payload["records"][0]["source_url"] == "https://example.test/source"
    assert any("Bluebook-ready" in item for item in payload["limitations"])
