"""Reviewer resolution must stay within the matter's authorized team."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.chat_tools import handlers


class _Result:
    def __init__(self, users):
        self._users = users

    def scalars(self):
        return self

    def all(self):
        return list(self._users)


class _DB:
    def __init__(self, users):
        self.users = users

    async def execute(self, _statement):
        return _Result(self.users)


def _fixture():
    tenant_id = uuid4()
    actor_id = uuid4()
    staff = SimpleNamespace(id=uuid4())
    attorney = SimpleNamespace(id=uuid4())
    outsider = SimpleNamespace(id=uuid4())
    db = _DB([staff, attorney])
    context = CapabilityContext(
        db=db,
        user=SimpleNamespace(id=actor_id, tenant_id=tenant_id),
    )
    matter = SimpleNamespace(id=uuid4(), attorney_of_record_id=attorney.id)
    active = {user.id: user for user in (staff, attorney, outsider)}
    return context, matter, staff, attorney, outsider, active


@pytest.mark.asyncio
async def test_explicit_staff_reviewer_must_be_a_matter_member(monkeypatch):
    context, matter, _, attorney, outsider, active = _fixture()

    async def active_reviewer(_context, user_id):
        return active.get(user_id)

    async def capabilities(_db, user_id):
        return {"approve_legal_work"} if user_id == attorney.id else set()

    monkeypatch.setattr(handlers, "_active_reviewer", active_reviewer)
    monkeypatch.setattr(handlers, "get_user_capabilities", capabilities)

    with pytest.raises(CapabilityError) as exc:
        await handlers._resolve_document_reviewers(
            context,
            matter=matter,
            requested_staff_user_id=outsider.id,
            requested_attorney_user_id=attorney.id,
        )
    assert exc.value.code == "invalid_staff_reviewer"


@pytest.mark.asyncio
async def test_explicit_attorney_reviewer_must_belong_to_matter(monkeypatch):
    context, matter, staff, _, outsider, active = _fixture()

    async def active_reviewer(_context, user_id):
        return active.get(user_id)

    async def capabilities(_db, _user_id):
        return {"approve_legal_work"}

    monkeypatch.setattr(handlers, "_active_reviewer", active_reviewer)
    monkeypatch.setattr(handlers, "get_user_capabilities", capabilities)

    with pytest.raises(CapabilityError) as exc:
        await handlers._resolve_document_reviewers(
            context,
            matter=matter,
            requested_staff_user_id=staff.id,
            requested_attorney_user_id=outsider.id,
        )
    assert exc.value.code == "invalid_attorney_reviewer"


@pytest.mark.asyncio
async def test_auto_resolution_returns_two_distinct_matter_members(monkeypatch):
    context, matter, staff, attorney, _, active = _fixture()

    async def active_reviewer(_context, user_id):
        return active.get(user_id)

    async def capabilities(_db, user_id):
        return {"approve_legal_work"} if user_id == attorney.id else set()

    monkeypatch.setattr(handlers, "_active_reviewer", active_reviewer)
    monkeypatch.setattr(handlers, "get_user_capabilities", capabilities)

    staff_id, attorney_id = await handlers._resolve_document_reviewers(
        context,
        matter=matter,
        requested_staff_user_id=None,
        requested_attorney_user_id=None,
    )
    assert staff_id == staff.id
    assert attorney_id == attorney.id
    assert staff_id != attorney_id
