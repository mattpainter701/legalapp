"""Behavioral contracts for after-call prospect follow-through."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.contact import Contact
from app.models.prospect_follow_through import ProspectFollowThrough
from app.routers.intake_assistant import FollowThroughUpdate
from app.services import prospect_follow_through as service


class FakeDB:
    def __init__(self, scalar_values=(), *, rowcount=1):
        self.scalar_values = list(scalar_values)
        self.rowcount = rowcount
        self.added = []
        self.executed = []
        self.commits = 0

    async def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def execute(self, statement, *args, **kwargs):
        self.executed.append((statement, args, kwargs))
        return SimpleNamespace(rowcount=self.rowcount)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None

    async def refresh(self, _row):
        return None

    def add(self, row):
        self.added.append(row)


def test_follow_through_updates_require_optimistic_version():
    with pytest.raises(ValidationError):
        FollowThroughUpdate(next_action="Call the prospect")


@pytest.mark.asyncio
async def test_adopt_contact_only_does_not_claim_an_unrelated_active_task():
    tenant_id, user_id, contact_id = uuid4(), uuid4(), uuid4()
    db = FakeDB(
        [None, Contact(id=contact_id, tenant_id=tenant_id, is_active=True), None]
    )

    row, created, task = await service.adopt_prospect(
        db,
        tenant_id,
        user_id,
        lead_id=None,
        contact_id=contact_id,
        intake_communication_id=None,
        assigned_attorney_user_id=None,
        idempotency_key="call-1",
    )

    assert created is True
    assert task is None
    assert row.primary_task_id is None
    assert not any(type(item).__name__ == "Task" for item in db.added)


def test_canonical_task_reference_is_never_a_broad_contact_lookup():
    lead_id, communication_id = uuid4(), uuid4()
    assert (
        service.canonical_task_external_ref(
            lead_id=lead_id, communication_id=communication_id
        )
        == f"intake-dashboard:lead:{lead_id}:follow-up"
    )
    assert (
        service.canonical_task_external_ref(
            lead_id=None, communication_id=communication_id
        )
        == f"intake-dashboard:call:{communication_id}:general-task"
    )
    assert (
        service.canonical_task_external_ref(lead_id=None, communication_id=None) is None
    )


def test_adopt_initializes_attorney_review_and_action_window():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    action, due = service.decision_defaults("pursue", now)
    assert action
    assert due is not None and due > now
    assert service.decision_defaults("decline", now) == (None, None)


@pytest.mark.asyncio
async def test_attorney_validation_rejects_cross_tenant_or_inactive_users():
    tenant_id, attorney_id = uuid4(), uuid4()
    db = FakeDB([None])
    with pytest.raises(HTTPException) as denied:
        await service._validate_attorney(db, tenant_id, attorney_id)
    assert denied.value.status_code == 422


@pytest.mark.asyncio
async def test_transition_pursue_audits_note_and_requires_live_action(monkeypatch):
    tenant_id, user_id = uuid4(), uuid4()
    prospect = ProspectFollowThrough(
        id=uuid4(),
        tenant_id=tenant_id,
        contact_id=uuid4(),
        status="attorney_review",
        version=1,
        assigned_attorney_user_id=user_id,
    )
    db = FakeDB()
    monkeypatch.setattr(service, "get_prospect", _return_prospect(prospect))

    result = await service.transition_prospect(
        db,
        tenant_id,
        user_id,
        prospect,
        transition="pursue",
        expected_version=1,
        note="Attorney approved outreach",
    )

    assert result is prospect
    assert db.commits == 1
    audit = next(
        item for item in db.added if type(item).__name__ == "ProspectFollowThroughEvent"
    )
    assert audit.note == "Attorney approved outreach"
    update_values = db.executed[0][0].compile().params
    assert update_values["next_action"]
    assert update_values["next_action_due_at"] is not None


@pytest.mark.asyncio
async def test_decline_clears_action_and_wrong_attorney_is_rejected(monkeypatch):
    tenant_id, assigned_id = uuid4(), uuid4()
    prospect = ProspectFollowThrough(
        id=uuid4(),
        tenant_id=tenant_id,
        contact_id=uuid4(),
        status="attorney_review",
        version=1,
        assigned_attorney_user_id=assigned_id,
        next_action="Call",
        next_action_due_at=datetime.now(timezone.utc),
    )
    db = FakeDB()
    with pytest.raises(HTTPException) as denied:
        await service.transition_prospect(
            db, tenant_id, uuid4(), prospect, transition="decline", expected_version=1
        )
    assert denied.value.status_code == 403

    monkeypatch.setattr(service, "get_prospect", _return_prospect(prospect))
    await service.transition_prospect(
        db, tenant_id, assigned_id, prospect, transition="decline", expected_version=1
    )
    values = db.executed[-1][0].compile().params
    assert values["next_action"] is None
    assert values["next_action_due_at"] is None


@pytest.mark.asyncio
async def test_stale_version_is_rejected_before_mutation():
    tenant_id, user_id = uuid4(), uuid4()
    prospect = ProspectFollowThrough(
        id=uuid4(),
        tenant_id=tenant_id,
        contact_id=uuid4(),
        status="attorney_review",
        version=3,
        assigned_attorney_user_id=user_id,
    )
    db = FakeDB()
    with pytest.raises(HTTPException) as stale:
        await service.transition_prospect(
            db, tenant_id, user_id, prospect, transition="pursue", expected_version=2
        )
    assert stale.value.status_code == 409
    assert not db.executed


def _return_prospect(prospect):
    async def _get(_db, _tenant_id, _prospect_id):
        return prospect

    return _get
