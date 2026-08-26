"""Behavioral contracts for after-call prospect follow-through."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models.contact import Contact
from app.models.prospect_follow_through import ProspectFollowThrough
from app.models.task import Task
from app.routers import intake_assistant as router
from app.routers.intake_assistant import (
    FollowThroughPrepareRequest,
    FollowThroughUpdate,
)
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
    assert service.decision_defaults("needs_information", now)[0]
    with pytest.raises(HTTPException):
        service.decision_defaults("unknown", now)


def test_router_helpers_normalize_dates_and_response_metadata():
    assert router._due_datetime(None) is None
    due = router._due_datetime(datetime(2026, 8, 26).date())
    assert due.hour == 17 and due.tzinfo is timezone.utc
    row = ProspectFollowThrough(
        id=uuid4(),
        lead_id=uuid4(),
        contact_id=uuid4(),
        status="pursuing",
        version=2,
        next_action_due_at=due,
        metadata_json={"assistant_preparation": {"suggestion": {"brief": "x"}}},
    )
    payload = router._response(row)
    assert (
        payload["decision"] == "pursue" and payload["next_action_date"] == "2026-08-26"
    )
    assert FollowThroughPrepareRequest(force=True).force is True


@pytest.mark.asyncio
async def test_attorney_validation_rejects_cross_tenant_or_inactive_users():
    tenant_id, attorney_id = uuid4(), uuid4()
    db = FakeDB([None])
    with pytest.raises(HTTPException) as denied:
        await service._validate_attorney(db, tenant_id, attorney_id)
    assert denied.value.status_code == 422


@pytest.mark.asyncio
async def test_get_prospect_and_adopt_reject_invalid_lead_inputs():
    tenant_id, user_id, lead_id, contact_id = uuid4(), uuid4(), uuid4(), uuid4()
    with pytest.raises(HTTPException) as absent:
        await service.get_prospect(FakeDB([None]), tenant_id, uuid4())
    assert absent.value.status_code == 404
    with pytest.raises(HTTPException) as no_lead:
        await service.adopt_prospect(
            FakeDB([None]),
            tenant_id,
            user_id,
            lead_id=lead_id,
            contact_id=contact_id,
            intake_communication_id=None,
            assigned_attorney_user_id=None,
            idempotency_key="missing",
        )
    assert no_lead.value.status_code == 404
    lead = SimpleNamespace(id=lead_id, contact_id=contact_id)
    with pytest.raises(HTTPException) as mismatch:
        await service.adopt_prospect(
            FakeDB([None, lead]),
            tenant_id,
            user_id,
            lead_id=lead_id,
            contact_id=uuid4(),
            intake_communication_id=None,
            assigned_attorney_user_id=None,
            idempotency_key="mismatch",
        )
    assert mismatch.value.status_code == 422


@pytest.mark.asyncio
async def test_existing_primary_task_is_loaded_without_creating_another():
    row = ProspectFollowThrough(
        id=uuid4(), tenant_id=uuid4(), contact_id=uuid4(), primary_task_id=uuid4()
    )
    task = Task(id=row.primary_task_id, tenant_id=row.tenant_id)
    assert (
        await service._ensure_primary_task(
            FakeDB([task]), row.tenant_id, uuid4(), row, assigned_attorney_user_id=None
        )
        is task
    )


@pytest.mark.asyncio
async def test_router_lead_and_prospect_not_found_errors():
    tenant_id = uuid4()
    with pytest.raises(HTTPException) as lead_error:
        await router._lead(FakeDB([None]), tenant_id, uuid4())
    assert lead_error.value.status_code == 404
    with pytest.raises(HTTPException) as prospect_error:
        await router._prospect(FakeDB([None]), tenant_id, uuid4())
    assert prospect_error.value.status_code == 404


@pytest.mark.asyncio
async def test_router_prepare_validates_communication_and_adopts(monkeypatch):
    tenant_id, actor_id = uuid4(), uuid4()
    lead = SimpleNamespace(id=uuid4(), contact_id=uuid4(), assigned_to_user_id=actor_id)
    prospect = ProspectFollowThrough(
        id=uuid4(),
        lead_id=lead.id,
        contact_id=lead.contact_id,
        status="attorney_review",
        version=1,
    )
    lead.assigned_to_user_id = actor_id
    user = SimpleNamespace(tenant_id=tenant_id, id=actor_id)
    monkeypatch.setattr(router, "set_tenant_context", lambda *_args: _async_none())
    monkeypatch.setattr(router, "_lead", lambda *_args: _async_value(lead))
    monkeypatch.setattr(
        router,
        "adopt_prospect",
        lambda *_args, **_kwargs: _async_value((prospect, False, None)),
    )
    prepared = {"suggestion": {"brief": "ready"}, "inference_available": False}
    prospect.metadata_json = {"assistant_preparation": prepared}
    monkeypatch.setattr(
        router,
        "prepare_after_call_handoff",
        lambda *_args, **_kwargs: _async_value(prepared),
    )
    result = await router.prepare_lead_follow_through(
        lead.id, FollowThroughPrepareRequest(), user, FakeDB()
    )
    assert result["suggestion"]["brief"] == "ready"

    class BadCommunicationDB(FakeDB):
        pass

    with pytest.raises(HTTPException) as invalid:
        await router.prepare_lead_follow_through(
            lead.id,
            FollowThroughPrepareRequest(communication_id=uuid4()),
            user,
            BadCommunicationDB([None]),
        )
    assert invalid.value.status_code == 422


@pytest.mark.asyncio
async def test_router_update_covers_decision_and_detail_paths(monkeypatch):
    tenant_id, actor_id = uuid4(), uuid4()
    prospect = ProspectFollowThrough(
        id=uuid4(),
        lead_id=uuid4(),
        contact_id=uuid4(),
        status="attorney_review",
        version=1,
    )
    prospect.assigned_attorney_user_id = actor_id
    user = SimpleNamespace(tenant_id=tenant_id, id=actor_id)
    monkeypatch.setattr(router, "set_tenant_context", lambda *_args: _async_none())
    monkeypatch.setattr(router, "_prospect", lambda *_args: _async_value(prospect))

    async def _transition(*_args, **_kwargs):
        prospect.status = "pursuing"
        return prospect

    monkeypatch.setattr(router, "transition_prospect", _transition)
    decision = await router.update_lead_follow_through(
        prospect.lead_id,
        FollowThroughUpdate(decision="pursue", expected_version=1),
        user,
        FakeDB(),
    )
    assert decision["decision"] == "pursue"
    with pytest.raises(HTTPException) as denied:
        prospect.assigned_attorney_user_id = uuid4()
        await router.update_lead_follow_through(
            prospect.lead_id,
            FollowThroughUpdate(next_action="Call", expected_version=1),
            user,
            FakeDB(),
        )
    assert denied.value.status_code == 403
    prospect.assigned_attorney_user_id = actor_id
    db = FakeDB(rowcount=1)
    updated = await router.update_lead_follow_through(
        prospect.lead_id,
        FollowThroughUpdate(
            next_action=" Call ",
            next_action_date=datetime(2026, 8, 30).date(),
            note="updated",
            expected_version=1,
        ),
        user,
        db,
    )
    assert updated["decision"] == "pursue" and db.commits == 1


async def _async_value(value):
    return value


async def _async_none(*_args, **_kwargs):
    return None


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


@pytest.mark.asyncio
async def test_adopt_rejects_missing_identity_and_idempotency_collision():
    tenant_id, user_id = uuid4(), uuid4()
    with pytest.raises(HTTPException) as missing:
        await service.adopt_prospect(
            FakeDB(),
            tenant_id,
            user_id,
            lead_id=None,
            contact_id=None,
            intake_communication_id=None,
            assigned_attorney_user_id=None,
            idempotency_key="x",
        )
    assert missing.value.status_code == 422
    existing = ProspectFollowThrough(
        id=uuid4(),
        tenant_id=tenant_id,
        lead_id=uuid4(),
        contact_id=uuid4(),
        idempotency_key="x",
        status="attorney_review",
        version=1,
    )
    with pytest.raises(HTTPException) as collision:
        await service.adopt_prospect(
            FakeDB([existing]),
            tenant_id,
            user_id,
            lead_id=uuid4(),
            contact_id=None,
            intake_communication_id=None,
            assigned_attorney_user_id=None,
            idempotency_key="x",
        )
    assert collision.value.status_code == 409


@pytest.mark.asyncio
async def test_adopt_existing_creates_missing_task_and_converts_concurrent_insert():
    tenant_id, user_id, contact_id = uuid4(), uuid4(), uuid4()
    existing = ProspectFollowThrough(
        id=uuid4(),
        tenant_id=tenant_id,
        contact_id=contact_id,
        intake_communication_id=uuid4(),
        idempotency_key="k",
        status="attorney_review",
        version=1,
    )
    # by-key hit takes the idempotent path and creates the canonical task.
    db = FakeDB([existing, None])
    row, created, task = await service.adopt_prospect(
        db,
        tenant_id,
        user_id,
        lead_id=None,
        contact_id=contact_id,
        intake_communication_id=None,
        assigned_attorney_user_id=None,
        idempotency_key="k",
    )
    assert row is existing and not created and task is not None

    class FlushConflict(FakeDB):
        def __init__(self, values):
            super().__init__(values)
            self.flushes = 0

        async def flush(self):
            self.flushes += 1
            if self.flushes == 1:
                raise IntegrityError("insert", {}, Exception("duplicate"))

    lead_id = uuid4()
    lead = SimpleNamespace(id=lead_id, contact_id=contact_id)
    winner = ProspectFollowThrough(
        id=uuid4(),
        tenant_id=tenant_id,
        lead_id=lead_id,
        contact_id=contact_id,
        idempotency_key="race",
        status="attorney_review",
        version=1,
    )
    db = FlushConflict([None, lead, None, winner])
    row, created, _ = await service.adopt_prospect(
        db,
        tenant_id,
        user_id,
        lead_id=lead_id,
        contact_id=contact_id,
        intake_communication_id=None,
        assigned_attorney_user_id=None,
        idempotency_key="race",
    )
    assert row is winner and not created and db.commits == 1


@pytest.mark.asyncio
async def test_transition_validates_reassign_and_terminal_state_and_updates_task(
    monkeypatch,
):
    tenant_id, user_id, other_id = uuid4(), uuid4(), uuid4()
    prospect = ProspectFollowThrough(
        id=uuid4(),
        tenant_id=tenant_id,
        contact_id=uuid4(),
        status="attorney_review",
        version=1,
        assigned_attorney_user_id=user_id,
        primary_task_id=uuid4(),
    )
    with pytest.raises(HTTPException) as no_assignee:
        await service.transition_prospect(
            FakeDB(),
            tenant_id,
            user_id,
            prospect,
            transition="reassign",
            expected_version=1,
        )
    assert no_assignee.value.status_code == 422
    with pytest.raises(HTTPException) as terminal:
        await service.transition_prospect(
            FakeDB(),
            tenant_id,
            user_id,
            ProspectFollowThrough(
                id=uuid4(),
                tenant_id=tenant_id,
                contact_id=uuid4(),
                status="declined",
                version=1,
                assigned_attorney_user_id=user_id,
            ),
            transition="pursue",
            expected_version=1,
        )
    assert terminal.value.status_code == 409
    monkey_db = FakeDB(
        [SimpleNamespace(id=other_id, tenant_id=tenant_id, is_active=True)]
    )

    async def _same(*_args):
        return prospect

    # The unit fake does not emulate a post-update SELECT.
    monkeypatch.setattr(service, "get_prospect", _same)
    result = await service.transition_prospect(
        monkey_db,
        tenant_id,
        user_id,
        prospect,
        transition="reassign",
        expected_version=1,
        assigned_attorney_user_id=other_id,
        note="Moved",
    )
    assert result is prospect
    assert len(monkey_db.executed) == 2
    assert any(
        type(item).__name__ == "ProspectFollowThroughEvent" for item in monkey_db.added
    )
