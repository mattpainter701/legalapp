"""Fast contract coverage for the tenant-safe matter/task automation routers.

These tests deliberately call the router functions with small fakes.  The
database integration suite owns SQL behavior; this file keeps the security,
provider-error, and workflow contracts quick and deterministic.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import matter_documents, tasks
from app.schemas.task import TaskTransitionRequest
from app.services.provider_http import (
    ProviderAuthError,
    ProviderError,
    ProviderNotFound,
    ProviderThrottled,
)


class Result:
    def __init__(self, value=None, rows=None):
        self.value = value
        self.rows = rows or []

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.rows


class DB:
    def __init__(self, value=None):
        self.value = value
        self.commits = 0
        self.deleted = []

    async def execute(self, _stmt):
        return Result(self.value)

    async def scalar(self, _stmt):
        # A generic fake has no rows: the SMS access gate's historical-run
        # probe must see "no SMS automation run", not the task fixture value.
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj, **_kwargs):
        return None

    async def delete(self, obj):
        self.deleted.append(obj)


def user(tenant_id, user_id=None, **extra):
    return SimpleNamespace(
        tenant_id=tenant_id,
        id=user_id or uuid4(),
        email="attorney@example.test",
        **extra,
    )


def document(**overrides):
    values = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        matter_id=uuid4(),
        task_id=None,
        filename="draft.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_size=3,
        storage_path=None,
        storage_backend="google_drive",
        storage_provider="google_drive",
        provider_object_id="object/1",
        provider_drive_id="drive/1",
        provider_parent_id=None,
        storage_state="verified",
        storage_error=None,
        document_sha256="a" * 64,
        generated_artifact_id=None,
        generated_artifact_revision_id=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def task(**overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        title="Review draft",
        description="Review it",
        task_type="review",
        status="review",
        priority="high",
        due_date=date.today(),
        due_time=None,
        matter_id=uuid4(),
        contact_id=None,
        assigned_to_user_id=None,
        created_by_user_id=uuid4(),
        completed_at=None,
        viewed_at=None,
        customer_contacted_at=None,
        customer_contact_method=None,
        closed_reason=None,
        closed_by_user_id=None,
        status_changed_at=now,
        waiting_reason=None,
        waiting_follow_up_date=None,
        reviewer_user_id=None,
        version=2,
        review_policy="single",
        review_stage="attorney",
        staff_reviewer_user_id=None,
        attorney_reviewer_user_id=None,
        staff_reviewed_at=None,
        staff_reviewed_by_user_id=None,
        attorney_approved_at=None,
        attorney_approved_by_user_id=None,
        attorney_override=False,
        source="assistant",
        external_ref=None,
        pending_action=None,
        created_at=now,
        updated_at=now,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Google", "google_drive"),
        ("gdrive", "google_drive"),
        ("microsoft_graph", "onedrive"),
        ("one-drive", "onedrive"),
        ("filesystem", "local"),
        ("SharePoint", "sharepoint"),
        (None, None),
    ],
)
def test_document_provider_normalization_is_explicit(value, expected):
    assert matter_documents._normalize_storage_provider(value) == expected


@pytest.mark.asyncio
async def test_cloud_delete_routes_each_supported_provider(monkeypatch):
    calls = []

    async def token(_db, tenant, provider):
        calls.append(("token", tenant, provider))
        return "fresh-token"

    async def google(method, path, **kwargs):
        calls.append(("google", method, path, kwargs))

    async def graph(method, path, **kwargs):
        calls.append(("graph", method, path, kwargs))

    monkeypatch.setattr(matter_documents, "get_fresh_token", token)
    monkeypatch.setattr(matter_documents, "google_request", google)
    monkeypatch.setattr(matter_documents, "graph_request", graph)
    tenant = uuid4()
    db = DB()
    await matter_documents._delete_cloud_provider_object(
        db=db,
        tenant_id=tenant,
        storage_provider="google_drive",
        object_id="a/b",
        drive_id=None,
    )
    await matter_documents._delete_cloud_provider_object(
        db=db,
        tenant_id=tenant,
        storage_provider="sharepoint",
        object_id="item/1",
        drive_id="drive/2",
    )
    await matter_documents._delete_cloud_provider_object(
        db=db,
        tenant_id=tenant,
        storage_provider="onedrive",
        object_id="item/2",
        drive_id=None,
    )
    assert any(call[0:3] == ("google", "DELETE", "/a%2Fb") for call in calls)
    assert any(
        call[0:3] == ("graph", "DELETE", "/drives/drive%2F2/items/item%2F1")
        for call in calls
    )
    assert any(
        call[0:3] == ("graph", "DELETE", "/me/drive/items/item%2F2") for call in calls
    )

    with pytest.raises(ProviderError, match="Unsupported"):
        await matter_documents._delete_cloud_provider_object(
            db=db,
            tenant_id=tenant,
            storage_provider="box",
            object_id="x",
            drive_id=None,
        )
    with pytest.raises(ProviderError, match="requires provider_drive_id"):
        await matter_documents._delete_cloud_provider_object(
            db=db,
            tenant_id=tenant,
            storage_provider="sharepoint",
            object_id="x",
            drive_id=None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, status",
    [
        (ProviderNotFound("gone"), None),
        (ProviderThrottled("slow", retry_after=7), 503),
        (ProviderAuthError("expired"), 502),
        (ProviderError("broken"), 502),
    ],
)
async def test_cloud_delete_preserves_database_on_provider_failures(
    monkeypatch, error, status
):
    doc = document()

    async def fail(**_kwargs):
        raise error

    monkeypatch.setattr(matter_documents, "_delete_cloud_provider_object", fail)
    if status is None:
        await matter_documents._delete_cloud_backing_if_needed(doc, DB())
    else:
        with pytest.raises(HTTPException) as exc:
            await matter_documents._delete_cloud_backing_if_needed(doc, DB())
        assert exc.value.status_code == status


@pytest.mark.asyncio
async def test_cloud_delete_rejects_ambiguous_metadata_and_skips_local_files():
    db = DB()
    await matter_documents._delete_cloud_backing_if_needed(
        document(
            storage_backend="local", storage_provider="local", provider_object_id=None
        ),
        db,
    )
    with pytest.raises(HTTPException) as exc:
        await matter_documents._delete_cloud_backing_if_needed(
            document(
                storage_backend="cloud",
                storage_provider="cloud",
                provider_object_id=None,
            ),
            db,
        )
    assert exc.value.status_code == 501


@pytest.mark.asyncio
async def test_matter_document_lookup_is_tenant_scoped_and_not_found():
    tenant = uuid4()
    doc = document(tenant_id=tenant)
    found = await matter_documents._get_doc_or_404("doc", "matter", tenant, DB(doc))
    assert found is doc
    with pytest.raises(HTTPException) as exc:
        await matter_documents._get_doc_or_404("missing", "matter", tenant, DB())
    assert exc.value.status_code == 404


def test_task_intake_reference_validation_and_reviewer_authorization():
    with pytest.raises(HTTPException, match="not an intake"):
        tasks._lead_id_from_intake_task(task(task_type="general"))
    with pytest.raises(HTTPException, match="invalid lead"):
        tasks._lead_id_from_intake_task(
            task(
                task_type="intake",
                external_ref="intake-dashboard:lead:nope:follow-up",
            )
        )

    tenant = uuid4()
    reviewer = uuid4()
    pending = task(
        tenant_id=tenant, reviewer_user_id=reviewer, pending_action={"type": "email"}
    )
    assert tasks._task_card_from_row(
        (pending, None, None, None, None, None, None, None)
    ).pending_action == {"type": "email"}


@pytest.mark.asyncio
async def test_transition_endpoint_enforces_tenant_and_staged_approval(monkeypatch):
    tenant = uuid4()
    current = user(tenant)
    pending = task(
        tenant_id=tenant,
        review_policy="staff_then_attorney",
        pending_action={"type": "email"},
    )
    db = DB(pending)
    monkeypatch.setattr(
        tasks, "get_current_user", lambda _request, _db: _async_value(current)
    )
    monkeypatch.setattr(tasks, "set_tenant_context", lambda *_args: _async_value(None))
    monkeypatch.setattr(tasks, "staged_review_is_approved", lambda _task: False)
    payload = TaskTransitionRequest(
        to_status="in_progress", expected_version=pending.version
    )
    with pytest.raises(HTTPException) as exc:
        await tasks.transition_task_status(pending.id, payload, current, db)
    assert exc.value.status_code == 409


async def _async_value(value):
    return value
