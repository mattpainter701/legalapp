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
from app.schemas.research_workspace import (
    MemberUpsert,
    RecordCreate,
    RecordUpdate,
    SnapshotCreate,
    WorkspaceCreate,
)


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
        if hasattr(row, "updated_at"):
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
    assert RecordCreate(record_type="memo", title="  Trimmed  ").title == "Trimmed"
    with pytest.raises(ValidationError, match="title must not be blank"):
        WorkspaceCreate(title="   ")
    with pytest.raises(ValidationError, match="title must not be blank"):
        RecordCreate(record_type="memo", title="   ")
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


@pytest.mark.asyncio
async def test_workspace_listing_archive_and_member_listing_are_scoped(monkeypatch):
    user, matter_id, workspace_id = actor(), uuid.uuid4(), uuid.uuid4()
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    member = SimpleNamespace(user_id=user.id, role="owner", revoked_at=None)
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "_matter", AsyncMock())
    listed = await router.list_workspaces(
        matter_id, DB(Result(rows=[(workspace, "owner")])), user
    )
    assert listed["items"][0]["id"] == workspace_id

    monkeypatch.setattr(
        router, "_workspace", AsyncMock(return_value=(workspace, member))
    )
    members = await router.list_members(
        matter_id, workspace_id, DB(Result(rows=[member])), user
    )
    assert members["items"] == [
        {"user_id": user.id, "role": "owner", "revoked_at": None}
    ]

    db = DB()
    archived = await router.archive_workspace(matter_id, workspace_id, db, user)
    assert archived.status_code == 204
    assert workspace.deleted_at is not None
    event = next(row for row in db.added if isinstance(row, ResearchWorkspaceEvent))
    assert event.detail["before"]["deleted_at"] is None
    assert event.detail["after"]["deleted_at"] is not None


@pytest.mark.asyncio
async def test_member_add_and_revoke_record_auditable_changes(monkeypatch):
    user, matter_id, workspace_id, invited_id = (
        actor(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    owner = SimpleNamespace(role="owner")
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router, "_workspace", AsyncMock(return_value=(workspace, owner))
    )
    monkeypatch.setattr(router, "_assert_same_tenant_user", AsyncMock())
    add_db = DB(Result(row=None))
    added = await router.upsert_member(
        matter_id,
        workspace_id,
        MemberUpsert(user_id=invited_id, role="editor"),
        add_db,
        user,
    )
    assert added["user_id"] == invited_id
    assert any(
        isinstance(row, ResearchWorkspaceEvent) and row.action == "member_added"
        for row in add_db.added
    )

    active_member = SimpleNamespace(user_id=invited_id, role="editor", revoked_at=None)
    revoke_db = DB(Result(row=active_member))
    response = await router.revoke_member(
        matter_id, workspace_id, invited_id, revoke_db, user
    )
    assert response.status_code == 204
    assert active_member.revoked_at is not None
    assert any(
        isinstance(row, ResearchWorkspaceEvent) and row.action == "member_revoked"
        for row in revoke_db.added
    )


@pytest.mark.asyncio
async def test_record_create_list_update_and_archive_preserve_history(monkeypatch):
    user, matter_id, workspace_id, record_id = (
        actor(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    member = SimpleNamespace(role="editor")
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router, "_workspace", AsyncMock(return_value=(workspace, member))
    )
    reviewer_check = AsyncMock()
    folder_check = AsyncMock()
    monkeypatch.setattr(router, "_assert_active_workspace_member", reviewer_check)
    monkeypatch.setattr(router, "_assert_active_folder", folder_check)

    create_db = DB()
    reviewer_id, folder_id = uuid.uuid4(), uuid.uuid4()
    created = await router.create_record(
        matter_id,
        workspace_id,
        RecordCreate(
            record_type="memo",
            title="Initial",
            body="Machine synthesis",
            assigned_reviewer_id=reviewer_id,
            folder_id=folder_id,
        ),
        create_db,
        user,
    )
    assert created["evidence_class"] == "model"
    assert any(isinstance(row, ResearchRecordRevision) for row in create_db.added)
    reviewer_check.assert_awaited_once_with(create_db, workspace, reviewer_id)
    folder_check.assert_awaited_once_with(create_db, workspace, folder_id)

    existing = ResearchRecord(
        id=record_id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="memo",
        title="Initial",
        body="Machine synthesis",
        evidence_class="model",
        revision=1,
    )
    updated = ResearchRecord(
        id=record_id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="memo",
        title="Revised",
        body="Attorney review needed",
        evidence_class="verify",
        revision=2,
    )
    update_db = DB(Result(row=existing), Result(row=updated))
    response = await router.update_record(
        matter_id,
        workspace_id,
        record_id,
        RecordUpdate(
            record_type="memo",
            title="Revised",
            body="Attorney review needed",
            evidence_class="verify",
            revision=1,
        ),
        update_db,
        user,
    )
    assert response["revision"] == 2
    event = next(row for row in update_db.added if isinstance(row, ResearchWorkspaceEvent))
    assert event.detail["before"]["evidence_class"] == "model"
    assert event.detail["after"]["evidence_class"] == "verify"

    records = await router.list_records(
        matter_id, workspace_id, DB(Result(rows=[updated])), user
    )
    assert records["items"][0]["title"] == "Revised"

    archived = ResearchRecord(
        id=record_id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="memo",
        title="Revised",
        evidence_class="verify",
        revision=3,
        deleted_at=datetime.now(timezone.utc),
    )
    archive_db = DB(Result(row=updated), Result(row=archived))
    archived_response = await router.archive_record(
        matter_id, workspace_id, record_id, archive_db, user
    )
    assert archived_response.status_code == 204
    archive_event = next(
        row for row in archive_db.added if isinstance(row, ResearchWorkspaceEvent)
    )
    assert archive_event.detail["after"]["revision"] == 3


@pytest.mark.asyncio
async def test_snapshots_export_and_history_keep_reviewable_provenance(monkeypatch):
    user, matter_id, workspace_id, snapshot_id = (
        actor(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    member = SimpleNamespace(role="editor")
    cited = ResearchRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        record_type="authority",
        title="Source",
        evidence_class="cited",
        source_url="https://example.test/source",
        revision=1,
    )
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router, "_workspace", AsyncMock(return_value=(workspace, member))
    )
    monkeypatch.setattr(router, "_idempotency_lock", AsyncMock())
    monkeypatch.setattr(router, "_idempotent_response", AsyncMock(return_value=None))
    reservation = SimpleNamespace(response_json=None)
    monkeypatch.setattr(
        router, "_reserve_idempotency", AsyncMock(return_value=reservation)
    )
    snapshot_db = DB(Result(scalar=1), Result(rows=[cited]))
    created = await router.create_snapshot(
        matter_id,
        workspace_id,
        SnapshotCreate(label="Attorney review"),
        "snapshot-key-1",
        snapshot_db,
        user,
    )
    assert created["sequence"] == 1
    snapshot = next(
        row for row in snapshot_db.added if row.__class__.__name__ == "ResearchWorkspaceSnapshot"
    )
    snapshot.id = snapshot_id

    listed = await router.list_snapshots(
        matter_id, workspace_id, DB(Result(rows=[snapshot])), user
    )
    assert listed["items"][0]["sha256"] == created["sha256"]
    exported = await router.export_snapshot(
        matter_id, workspace_id, snapshot_id, DB(Result(row=snapshot)), user
    )
    assert exported.headers["x-research-snapshot-sha256"] == created["sha256"]
    with pytest.raises(Exception) as missing_export:
        await router.export_snapshot(
            matter_id, workspace_id, uuid.uuid4(), DB(Result(row=None)), user
        )
    assert missing_export.value.status_code == 404

    event = SimpleNamespace(
        id=uuid.uuid4(),
        record_id=cited.id,
        action="snapshot_created",
        detail={"snapshot": created},
        actor_user_id=user.id,
        created_at=datetime.now(timezone.utc),
    )
    history = await router.workspace_history(
        matter_id, workspace_id, DB(Result(rows=[event])), user
    )
    assert history["items"][0]["detail"]["snapshot"]["sha256"] == created["sha256"]


@pytest.mark.asyncio
async def test_create_replays_are_side_effect_free(monkeypatch):
    user, matter_id, workspace_id = actor(), uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(router, "_matter", AsyncMock())
    monkeypatch.setattr(router, "_idempotency_lock", AsyncMock())
    monkeypatch.setattr(
        router, "_idempotent_response", AsyncMock(return_value={"id": "replayed"})
    )
    reserve = AsyncMock()
    monkeypatch.setattr(router, "_reserve_idempotency", reserve)
    workspace_db = DB()
    assert await router.create_workspace(
        matter_id, WorkspaceCreate(title="Research"), "workspace-key-2", workspace_db, user
    ) == {"id": "replayed"}
    assert workspace_db.added == []

    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    monkeypatch.setattr(
        router,
        "_workspace",
        AsyncMock(return_value=(workspace, SimpleNamespace(role="editor"))),
    )
    snapshot_db = DB()
    assert await router.create_snapshot(
        matter_id,
        workspace_id,
        SnapshotCreate(),
        "snapshot-key-2",
        snapshot_db,
        user,
    ) == {"id": "replayed"}
    assert snapshot_db.added == []
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotency_replay_is_stable_and_rejects_misuse():
    user = actor()
    request = {"matter_id": str(uuid.uuid4()), "title": "Research"}
    stored = SimpleNamespace(
        request_sha256=router._digest(request), response_json={"id": "stored"}
    )
    assert await router._idempotent_response(
        DB(Result(row=stored)), user, "workspace_create", "stable-key", request
    ) == {"id": "stored"}
    with pytest.raises(Exception) as mismatch:
        await router._idempotent_response(
            DB(Result(row=stored)),
            user,
            "workspace_create",
            "stable-key",
            {"matter_id": request["matter_id"], "title": "Different"},
        )
    assert mismatch.value.status_code == 409
    pending = SimpleNamespace(request_sha256=router._digest(request), response_json=None)
    with pytest.raises(Exception) as in_progress:
        await router._idempotent_response(
            DB(Result(row=pending)), user, "workspace_create", "stable-key", request
        )
    assert in_progress.value.status_code == 409
    assert (
        await router._idempotent_response(
            DB(Result(row=None)), user, "workspace_create", "new-key", request
        )
        is None
    )


@pytest.mark.asyncio
async def test_member_and_record_denial_paths_preserve_collaboration_guards(
    monkeypatch,
):
    user, matter_id, workspace_id, owner_id, record_id = (
        actor(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    owner = SimpleNamespace(user_id=owner_id, role="owner", revoked_at=None)
    monkeypatch.setattr(router, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        router,
        "_workspace",
        AsyncMock(return_value=(workspace, SimpleNamespace(role="owner"))),
    )
    monkeypatch.setattr(router, "_assert_same_tenant_user", AsyncMock())
    with pytest.raises(Exception) as demotion:
        await router.upsert_member(
            matter_id,
            workspace_id,
            MemberUpsert(user_id=owner_id, role="viewer"),
            DB(Result(row=owner), Result(scalar=1)),
            user,
        )
    assert demotion.value.status_code == 409

    monkeypatch.setattr(
        router,
        "_workspace",
        AsyncMock(return_value=(workspace, SimpleNamespace(role="reviewer"))),
    )
    with pytest.raises(Exception) as reviewer_denied:
        await router.archive_record(
            matter_id, workspace_id, record_id, DB(), user
        )
    assert reviewer_denied.value.status_code == 403


@pytest.mark.asyncio
async def test_workspace_and_optional_record_references_fail_closed(monkeypatch):
    user, matter_id, workspace_id = actor(), uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(router, "_matter", AsyncMock())
    with pytest.raises(Exception) as missing_workspace:
        await router._workspace(matter_id, workspace_id, user, DB(Result(row=None)))
    assert missing_workspace.value.status_code == 404

    workspace = ResearchWorkspace(
        id=workspace_id, tenant_id=user.tenant_id, matter_id=matter_id, title="Research"
    )
    with pytest.raises(Exception) as missing_reviewer:
        await router._assert_active_workspace_member(
            DB(Result(row=None)), workspace, uuid.uuid4()
        )
    assert missing_reviewer.value.status_code == 409
    with pytest.raises(Exception) as missing_folder:
        await router._assert_active_folder(DB(Result(row=None)), workspace, uuid.uuid4())
    assert missing_folder.value.status_code == 409
